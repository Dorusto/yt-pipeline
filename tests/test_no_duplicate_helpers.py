import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASELINE = {
    "AMBIENT_PHRASES", "BACKENDS", "BATCH_CHARS", "BEAT_SNAP_FRACTION",
    "BRIEF_KEYWORDS", "CAMERA_PREFIX", "CLIP_COUNTER", "CONTENT_TYPES",
    "CONTIGUOUS_TOLERANCE_S", "DEFAULT_HEIGHT",
    "DEFAULT_IMPORTANT_MAX_S", "DEFAULT_TRACK", "DEFAULT_WIDTH",
    "DURATION_RE", "FPS", "FRAME_COUNT",
    "IMPORTANT_MOTION_LABELS", "IMPORTANT_SEMANTIC_MOTIONS",
    "INVALID_FRAME_RATES", "JPEG_QUALITY", "LANDING_FLOOR_S", "LANDING_GAP_S",
    "LONG_CUT_PACING_S", "MAX_LINE_CHARS", "MINUTE_UNITS", "MOVE_ROLES",
    "NON_MOVE_MOTION_LABELS", "ORDER_CHRONOLOGICAL", "ORDER_SCORE",
    "PACING_ANCHORS", "PHOTO_DEFAULT_S", "PHOTO_END_S", "PHOTO_MAX_S",
    "PHOTO_MIN_S", "PHOTO_SEGMENT_ID", "PHOTO_START_S", "REPO_ROOT",
    "SCHEMA_VERSION", "SCREEN_TOKEN", "SILENCE_FLOOR_DBFS",
    "SKIP_ROLES", "TOLERANCE_S", "TRAVEL_LABELS",
    "UNIFORM_PHRASES", "VIDEO_EXTENSIONS",
    "_atomic_write_json", "_clip_tokens", "_dbfs", "_ffprobe_path",
    "_load_json", "_matches", "_median", "_phrase_move", "_runs",
    "_to_analysis_gray", "atomic_write_json", "beats_by_segment_from_shotlog",
    "best_overlapping_move", "build_phrases", "candidate_key", "clamp",
    "clip_counter", "clip_position", "derive_beat_period", "find_clips",
    "find_photos", "first_not_none", "fmt", "get_client", "is_important",
    "is_number", "is_photo_slot", "is_within", "landing_pairs",
    "load_ingest_photos", "load_json", "load_shotlog", "nearest_beat",
    "normalize_timestamp", "order_clips", "pacing_table", "parse_duration",
    "parse_time", "screen_length", "segment_key", "segment_subranges",
    "shots_similar", "slot_duration", "snap_warning", "source_contiguous",
    "source_delta", "spent_usd", "timestamp_seconds", "write_json",
}


def normalise_function(node):
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    text = "\n".join(ast.unparse(statement) for statement in body)
    return " ".join(text.split())


def normalise_constant(node):
    return " ".join(ast.unparse(node.value).split())


def collect(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[(node.name, normalise_function(node))] = path
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    found[(target.id, normalise_constant(node))] = path
    return found


def duplicate_names():
    top_level = sorted((ROOT / "scripts").glob("*.py"))
    common = sorted((ROOT / "scripts" / "common").glob("*.py"))
    definitions = {}
    for path in top_level + common:
        for key, owner in collect(path).items():
            definitions.setdefault(key, set()).add(owner)
    names = set()
    for (name, _), owners in definitions.items():
        tops = {owner for owner in owners if owner.parent.name == "scripts"}
        commons = {owner for owner in owners if owner.parent.name == "common"}
        if len(tops) >= 2 or (tops and commons):
            names.add(name)
    return names


def test_no_new_duplicate_helpers():
    assert duplicate_names() - BASELINE == set()


def test_baseline_has_no_stale_entries():
    assert BASELINE - duplicate_names() == set()


def test_no_hand_rolled_worker_pool():
    offenders = []
    pool = ROOT / "scripts" / "common" / "pool.py"
    paths = sorted((ROOT / "scripts").glob("*.py")) + sorted(
        (ROOT / "scripts" / "common").glob("*.py")
    )
    for path in paths:
        if path == pool:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {
                "ThreadPoolExecutor",
                "ProcessPoolExecutor",
            }:
                offenders.append(path.name)
                break
            if isinstance(node, ast.Attribute) and node.attr in {
                "ThreadPoolExecutor",
                "ProcessPoolExecutor",
            }:
                offenders.append(path.name)
                break
    assert offenders == []
