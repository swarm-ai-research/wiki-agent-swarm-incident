"""Validation and non-destructive regeneration of evaluation artifacts."""
import json
import os
import tempfile
from pathlib import Path

REVIEW_FIELDS={'human_grade','adjudication','human_review'}
GENERATED_FIELDS={'blind_id','question','answer','reference_answer','kind','automated_grade','posthoc_literal_abstention','task','reference','outcome','citations'} | REVIEW_FIELDS
CONTEXT_FIELDS=('question','answer','reference_answer','reference','kind','task','citations')


def require_complete(report):
    expected={str(i) for i in range(report['limits']['agents'])}
    answers={str(i) for i in report['answers']}
    stopped={str(i) for i in report['stopped']}
    if answers & stopped or answers | stopped != expected:
        raise ValueError(f"Incomplete or invalid cohort: {report.get('task_id', 'unknown')}")


def require_manifest_runs(paths,manifest_path):
    manifest=json.loads(Path(manifest_path).read_text())
    expected=[job['id']+'.json' for job in manifest['jobs']]
    actual=[Path(p).name for p in paths]
    if not expected or len(expected)!=len(set(expected)) or set(actual)!=set(expected) or len(actual)!=len(expected):
        raise ValueError('Run files do not match the declared study manifest')


def preserve_reviews(path,rows):
    """Carry judgments and reviewer-added fields by ID; refuse unsafe remapping."""
    path=Path(path)
    previous=json.loads(path.read_text()) if path.exists() else []
    old={row['blind_id']:row for row in previous}
    current={row['blind_id']:dict(row) for row in rows}
    if len(old)!=len(previous) or len(current)!=len(rows):
        raise ValueError('Duplicate blind IDs in review packet')
    generated_fields=GENERATED_FIELDS.union(*(row.keys() for row in rows))
    for token,row in old.items():
        # Unknown fields may hold reviewer notes; preserve them too.
        judgments={k:v for k,v in row.items() if k in REVIEW_FIELDS or k not in generated_fields}
        has_review=any(v is not None for k,v in judgments.items() if k!='blind_id')
        if token not in current:
            if has_review:raise ValueError(f'Reviewed answer missing from regeneration: {token}')
            continue
        target=current[token]
        if has_review and any(row.get(k)!=target.get(k) for k in CONTEXT_FIELDS):
            raise ValueError(f'Reviewed answer context changed: {token}')
        target.update(judgments)
    return [current[row['blind_id']] for row in rows]


def write_json_atomic(path,value):
    path=Path(path)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix=path.name+'.',delete=False) as stream:
        temporary=Path(stream.name)
        try:
            json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
