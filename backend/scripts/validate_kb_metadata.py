import json
import sys
from pathlib import Path

REQUIRED_KEYS = [
    "section",
    "clause_type",
    "risk_level",
    "industry",
    "region",
    "deployment_model",
    "architecture_pattern",
    "service_family",
    "compliance_scope",
    "tags",
]


def validate_record(record: dict, line_no: int) -> list[str]:
    issues = []
    metadata = record.get("metadata", {})
    for key in REQUIRED_KEYS:
        if key not in metadata:
            issues.append(f"line {line_no}: missing metadata.{key}")
            continue
        value = metadata.get(key)
        if value in (None, "", []):
            issues.append(f"line {line_no}: empty metadata.{key}")

    tags = metadata.get("tags") or []
    if isinstance(tags, list) and len(tags) < 3:
        issues.append(f"line {line_no}: under-tagged clause (<3 tags)")

    return issues


def main(path: str) -> int:
    fp = Path(path)
    if not fp.exists():
        print(f"ERROR: file not found: {path}")
        return 2

    all_issues = []
    with fp.open() as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                all_issues.append(f"line {i}: invalid JSON")
                continue
            all_issues.extend(validate_record(record, i))

    if all_issues:
        print("KB metadata validation FAILED")
        for issue in all_issues[:200]:
            print(f" - {issue}")
        if len(all_issues) > 200:
            print(f" ... {len(all_issues)-200} more issues")
        return 1

    print("KB metadata validation PASSED")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python backend/scripts/validate_kb_metadata.py <kb_chunks.jsonl>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
