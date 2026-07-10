"""#312: get_table reuses one cached boto3 resource per region instead of
building a fresh one (a new TLS handshake) on every call."""
from unittest.mock import MagicMock, patch

from utils import dynamodb_helpers as h


def test_get_table_caches_resource_per_region():
    h._dynamo_resource.cache_clear()
    try:
        fake_resource = MagicMock(name="resource")
        with patch.object(h.boto3, "resource", return_value=fake_resource) as res:
            h.get_table("reciterai", region="us-cache-test-1")
            h.get_table("reciterai", region="us-cache-test-1")
            h.get_table("other", region="us-cache-test-1")
        # boto3.resource is built once for the region despite three get_table calls.
        assert res.call_count == 1
        # Each call still returns a Table bound to the requested name.
        assert fake_resource.Table.call_count == 3
    finally:
        h._dynamo_resource.cache_clear()
