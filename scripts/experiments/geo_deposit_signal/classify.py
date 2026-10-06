"""Facility classifiers shared by GEO-text and paper-text (label) matching."""
import re
HOME = r"(Weill[\s-]*Cornell|WCM|WCMC|Cornell\s+Medic|Cornell\s+University\s+Medical)"
# core-specific named facilities
EPI = re.compile(r"Epigenomics\s+Core", re.I)
GRCF = re.compile(r"Genomics\s+Resources?\s+Core|GRCF", re.I)
# generic "genomics/sequencing core" that must sit within 80 chars of a home-institution token
GEN_CORE = re.compile(r"(?<!epi)(genomics?|sequencing|genome)\s+(core|facility|resources?\s+core)", re.I)
HOME_RE = re.compile(HOME, re.I)
ABC = re.compile(r"Applied\s+Bioinformatics\s+Core", re.I)
EXTERNAL = re.compile(r"Novogene|GENEWIZ|Azenta|\bBGI\b|Beijing\s+Genomics|New\s+York\s+Genome\s+Center|\bNYGC\b|"
    r"Integrated\s+Genomics\s+Operation|\bIGO\b|Memorial\s+Sloan|\bMSKCC\b|Rockefeller\s+University\s+Genomics|"
    r"Genome\s+Technology\s+Center|Columbia\s+Genome\s+Center|Sulzberger|Admera|Psomagen|Macrogen|Broad\s+Institute|"
    r"Yale\s+Center\s+for\s+Genome|Hudson\s*Alpha|Novartis|Illumina\s+FastTrack|Genomics\s+Core\s+at\s+(?!Weill)|"
    r"\bUCSF\b|Mount\s+Sinai|NYU\s+(Langone|Genome)|Albert\s+Einstein|Princeton|Harvard|Dana[\s-]Farber|"
    r"Baylor|Stanford|Duke|Vanderbilt|Penn\s+(Genomics|Next)|University\s+of\s+Pennsylvania|Johns\s+Hopkins|Fred\s+Hutch|Jackson\s+Lab", re.I)

def home_generic(text):
    for m in GEN_CORE.finditer(text):
        win = text[max(0, m.start()-90): m.end()+90]
        if HOME_RE.search(win):
            return True
    return False

def facility_hits(text):
    """-> set of tags among {'core3','core5','core1','home_generic','external'}"""
    t = set()
    if not text: return t
    if EPI.search(text): t.add('core3')
    if GRCF.search(text): t.add('core5')
    if ABC.search(text): t.add('core1')
    if home_generic(text): t.add('home_generic')
    if EXTERNAL.search(text): t.add('external')
    return t
