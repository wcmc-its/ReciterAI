"""
Bedrock client for ReCiter AI Chatbot pipeline.

Ported and adapted from CViche's unified_pipeline/llm_client.py.

Provides BedrockClient with:
- boto3 Converse API with lazy client initialization
- Exponential backoff retry on transient errors (ThrottlingException, etc.)
- JSON fence stripping and validation with retry on non-JSON responses
- Reusable for both offline scoring pipeline (Phase 1) and chat runtime (Phase 2)

Usage:
    from utils.bedrock_client import BedrockClient, HAIKU_MODEL, SONNET_MODEL

    client = BedrockClient()
    result = client.call(
        model=HAIKU_MODEL,
        messages=[{"role": "user", "content": "Score this publication..."}],
        system="You are a scoring assistant.",
    )

    # For JSON responses:
    data = client.call_json(
        model=SONNET_MODEL,
        messages=[{"role": "user", "content": "Return topic scores as JSON..."}],
    )
"""

import boto3
import json
import re
import time
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Pinned model IDs (per RESEARCH.md — all ACTIVE in us-east-1)
HAIKU_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET_MODEL = "us.anthropic.claude-sonnet-4-6"
OPUS_MODEL = "us.anthropic.claude-opus-4-7"

# Stage-keyed view of the pinned model IDs. Used by Phase 9 substrate
# (utils.stage_records.compute_input_hash) so a model swap in any stage
# invalidates that stage's content-addressed skip cache automatically.
# Keys are stage names; values are the model ID strings above.
#
# Adding a stage: append a row here. Removing a stage: leave the key in
# place but point it at a comment explaining the deprecation, since
# input_hash records persisted in DynamoDB still reference it.
MODEL_IDS_BY_STAGE: dict[str, str] = {
    "screening":               HAIKU_MODEL,   # score_publications Pass 1
    "scoring":                 SONNET_MODEL,  # score_publications Pass 2
    "subtopic_discovery":      SONNET_MODEL,  # discover_subtopics
    "subtopic_assignment":     HAIKU_MODEL,   # assign_subtopics
    "subtopic_relabel":        SONNET_MODEL,  # relabel_subtopics (display_name/short_description)
    "taxonomy_generation":     SONNET_MODEL,  # generate_taxonomy
    "see_also_generation":     SONNET_MODEL,  # generate_see_also
    "spotlight_lede":          OPUS_MODEL,    # spotlight.lede_generator
    "spotlight_critic":        HAIKU_MODEL,   # spotlight.critic
}


class BedrockEmptyContentError(RuntimeError):
    """Raised when Bedrock Converse returns no content blocks.

    Happens when Bedrock's safety filter (stopReason='content_filtered' or
    'guardrail_intervened') blocks the response, leaving `output.message.content`
    as an empty list. Surfaces as a structured per-call failure with the
    stopReason in the message, instead of an opaque `IndexError: list index
    out of range` from `content[0]`.
    """

    def __init__(self, *, stop_reason: str, model: str):
        self.stop_reason = stop_reason
        self.model = model
        super().__init__(
            f"Bedrock returned empty content from model={model} "
            f"(stopReason={stop_reason!r})"
        )


@dataclass
class BedrockCallResult:
    """One Bedrock Converse call outcome — response text plus token usage.

    Returned by ``BedrockClient.call_with_usage``. ``call`` / ``call_json``
    discard the Converse ``usage`` block; callers that attribute per-call
    cost — the daily-enrichment synopsis + impact workers — need it, so
    this carries the ``usage.{inputTokens,outputTokens}`` counts and the
    ``stopReason`` alongside the response text.
    """

    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str


class BedrockClient:
    """
    Bedrock Converse API client with retry, JSON validation, and lazy initialization.

    Design decisions:
    - Lazy client init: boto3 client is NOT created in __init__, only on first call.
      This means imports don't require AWS credentials at import time.
    - Exponential backoff: sleeps min(2**attempt, 30) seconds between retries.
    - JSON retry: if call_json receives non-JSON, retries once with stronger hint.
    - Region: reads from AWS_DEFAULT_REGION env var (defaults to us-east-1).

    Security (T-01-02):
    - AWS credentials via default credential chain (IAM role or ~/.aws/credentials).
    - Never passed as parameters or logged.
    - Bedrock responses are not logged at DEBUG level to prevent faculty data disclosure.
    """

    # Bedrock error codes that should trigger retry (ported from CViche lines 54-58)
    RETRYABLE_CODES = frozenset({
        "ThrottlingException",
        "ModelTimeoutException",
        "InternalServerException",
        "ServiceUnavailableException",
    })

    def __init__(self, region: str = None):
        """
        Initialize the BedrockClient.

        Args:
            region: AWS region. If None, reads AWS_DEFAULT_REGION env var,
                    defaults to 'us-east-1'.

        Note: Does NOT create the boto3 client here. Client is created lazily
        on the first API call to avoid import-time side effects.
        """
        self.region = region or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        self._client = None  # Lazy init — set on first call to _get_client()

    def _get_client(self):
        """Get or create the Bedrock Runtime boto3 client (lazy initialization).

        Creates the client only once and caches it on self._client.
        Uses default AWS credential chain (env vars, ~/.aws/credentials, IAM role).
        """
        if self._client is None:
            from botocore.config import Config
            self._client = boto3.client(
                'bedrock-runtime',
                region_name=self.region,
                config=Config(read_timeout=900, retries={'max_attempts': 0}),
            )
        return self._client

    def call(
        self,
        model: str,
        messages: list,
        system: str = None,
        max_tokens: int = 4096,
        temperature: float | None = 0.0,
    ) -> str:
        """
        Make a Bedrock Converse API call and return the response text.

        Args:
            model: Bedrock model ID (e.g., HAIKU_MODEL, SONNET_MODEL, OPUS_MODEL).
            messages: List of message dicts in OpenAI-style format:
                      [{"role": "user", "content": "text"}]
            system: Optional system prompt string.
            max_tokens: Maximum tokens in response (default 4096).
            temperature: Sampling temperature (default 0.0 for deterministic scoring).
                Pass ``None`` for models that have deprecated temperature
                control (e.g., Opus 4.7). When None, temperature is omitted
                from inferenceConfig and the model uses its built-in default.

        Returns:
            The response text content as a string.

        Raises:
            botocore.exceptions.ClientError: On non-retryable Bedrock errors.
            BedrockEmptyContentError: If Bedrock returns no content blocks
                (e.g. stopReason='content_filtered'). Caller can inspect
                `stop_reason` to decide whether to retry or mark the unit
                failed.
        """
        messages_converse, system_list = self._translate_messages(messages, system)
        response = self._call_with_retry(
            model=model,
            messages_converse=messages_converse,
            system_list=system_list,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content_blocks = response.get('output', {}).get('message', {}).get('content') or []
        if not content_blocks:
            raise BedrockEmptyContentError(
                stop_reason=response.get('stopReason', 'unknown'),
                model=model,
            )
        return content_blocks[0]['text']

    def call_json(
        self,
        model: str,
        messages: list,
        system: str = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> dict:
        """
        Make a Bedrock Converse API call and return parsed JSON.

        Strips markdown fences (```json ... ```) before parsing.
        On JSONDecodeError, retries ONCE with a stronger hint appended to messages.

        Args:
            model: Bedrock model ID.
            messages: List of message dicts in OpenAI-style format.
            system: Optional system prompt string.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            Parsed dict from the JSON response.

        Raises:
            json.JSONDecodeError: If response is not valid JSON after retry.
            botocore.exceptions.ClientError: On non-retryable Bedrock errors.
        """
        content = self.call(
            model=model,
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Strip markdown fences (ported from CViche JSON validation, lines 369-389)
        cleaned = re.sub(r'```json\n?|\n?```', '', content).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(
                "Bedrock response is not valid JSON. Retrying with stronger hint..."
            )
            # Retry once with stronger hint appended
            retry_messages = list(messages) + [{
                "role": "user",
                "content": "Respond with valid JSON only. No markdown fences.",
            }]
            retry_content = self.call(
                model=model,
                messages=retry_messages,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            retry_cleaned = re.sub(r'```json\n?|\n?```', '', retry_content).strip()
            return json.loads(retry_cleaned)

    def call_with_usage(
        self,
        model: str,
        messages: list,
        system: str = None,
        max_tokens: int = 4096,
        temperature: float | None = 0.0,
    ) -> BedrockCallResult:
        """
        Make a Bedrock Converse API call and return text + token usage.

        Like ``call``, but returns a ``BedrockCallResult`` carrying the
        ``usage.{inputTokens,outputTokens}`` counts and the ``stopReason``,
        not just the response string. Used by callers that attribute
        per-call cost — the daily-enrichment synopsis + impact workers.

        ``call`` / ``call_json`` are deliberately left untouched so their
        existing callers (``score_publications`` etc.) are unaffected; the
        few lines of response handling below are intentionally duplicated
        rather than refactored into a shared private helper.

        Args:
            model: Bedrock model ID (e.g., HAIKU_MODEL, SONNET_MODEL).
            messages: OpenAI-style message list, as for ``call``.
            system: Optional system prompt string.
            max_tokens: Maximum tokens in response (default 4096).
            temperature: Sampling temperature (default 0.0). Pass ``None``
                to omit it — see ``call``.

        Returns:
            BedrockCallResult with the response text and token usage.

        Raises:
            BedrockEmptyContentError: If Bedrock returns no content blocks
                (e.g. stopReason='content_filtered'). Callers inspect
                ``stop_reason`` to branch to a content-filter fallback.
            botocore.exceptions.ClientError: On non-retryable Bedrock errors.
        """
        messages_converse, system_list = self._translate_messages(messages, system)
        response = self._call_with_retry(
            model=model,
            messages_converse=messages_converse,
            system_list=system_list,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content_blocks = response.get('output', {}).get('message', {}).get('content') or []
        stop_reason = response.get('stopReason', 'unknown')
        if not content_blocks:
            raise BedrockEmptyContentError(stop_reason=stop_reason, model=model)
        text = content_blocks[0].get('text') or ''
        # Whitespace-only text is functionally empty for JSON-returning enrichment
        # callers — observed 2026-05-20 11:01 UTC tick where a PMID's impact call
        # returned non-empty content_blocks with text that wouldn't json-parse,
        # so call_with_fallback's "transient empty retry" never fired and one bad
        # response failed the whole 36-PMID delta. Hoisting the empty-text check
        # here routes that case through the existing retry path.
        if not text.strip():
            raise BedrockEmptyContentError(stop_reason=stop_reason, model=model)
        usage = response.get('usage') or {}
        return BedrockCallResult(
            text=text,
            input_tokens=int(usage.get('inputTokens') or 0),
            output_tokens=int(usage.get('outputTokens') or 0),
            stop_reason=stop_reason,
        )

    def _call_with_retry(
        self,
        model: str,
        messages_converse: list,
        system_list: list,
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """
        Call Bedrock Converse API with exponential backoff retry.

        Retries up to 3 attempts on retryable error codes.
        Non-retryable errors raise immediately.

        Args:
            model: Bedrock model ID.
            messages_converse: Messages in Converse API format.
            system_list: System prompts in Converse API format (list of {"text": "..."}).
            max_tokens: Max tokens for inferenceConfig.
            temperature: Temperature for inferenceConfig.

        Returns:
            Raw Bedrock Converse API response dict.

        Raises:
            botocore.exceptions.ClientError: On non-retryable errors or exhausted retries.
        """
        from botocore.exceptions import ClientError, ReadTimeoutError

        client = self._get_client()

        inference_config: dict = {'maxTokens': max_tokens}
        # Opus 4.7 deprecates the temperature parameter — caller passes None
        # to skip it. Sonnet/Haiku still honor temperature.
        if temperature is not None:
            inference_config['temperature'] = float(temperature)

        call_kwargs = {
            'modelId': model,
            'messages': messages_converse,
            'inferenceConfig': inference_config,
        }
        if system_list:
            call_kwargs['system'] = system_list

        last_error = None
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                return client.converse(**call_kwargs)
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code not in self.RETRYABLE_CODES:
                    # Non-retryable: raise immediately
                    raise
                last_error = e
                if attempt < max_attempts - 1:
                    # Exponential backoff: 1s, 2s, 4s... capped at 30s
                    wait = min(2 ** attempt, 30)
                    logger.warning(
                        f"Bedrock call failed (attempt {attempt + 1}/{max_attempts}): "
                        f"{error_code}. Retrying in {wait}s..."
                    )
                    time.sleep(wait)
            except ReadTimeoutError as e:
                last_error = e
                if attempt < max_attempts - 1:
                    wait = min(2 ** attempt, 30)
                    logger.warning(
                        f"Bedrock read timeout (attempt {attempt + 1}/{max_attempts}). "
                        f"Retrying in {wait}s..."
                    )
                    time.sleep(wait)

        raise last_error

    def _translate_messages(
        self,
        messages: list,
        system: str = None,
    ) -> tuple:
        """
        Convert OpenAI-style messages to Bedrock Converse API format.

        OpenAI format: [{"role": "user", "content": "text"}]
        Converse format: [{"role": "user", "content": [{"text": "text"}]}]

        System messages in the messages list are extracted and merged with
        the system parameter (if provided).

        Args:
            messages: OpenAI-style message list.
            system: Optional system prompt string.

        Returns:
            Tuple of (converse_messages, system_list) where:
            - converse_messages: List of messages in Converse API format
            - system_list: List of system prompt dicts [{"text": "..."}] or []
        """
        converse_messages = []
        system_texts = []

        # Collect system prompt from parameter
        if system:
            system_texts.append(system)

        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')

            if role == 'system':
                # Extract system messages from the messages list
                system_texts.append(content)
            else:
                converse_messages.append({
                    'role': role,
                    'content': [{'text': content}],
                })

        # Combine all system texts into system_list
        if system_texts:
            combined_system = '\n\n'.join(system_texts)
            system_list = [{'text': combined_system}]
        else:
            system_list = []

        return converse_messages, system_list
