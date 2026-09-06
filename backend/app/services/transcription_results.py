"""Read complete candidates or durable partial results without publishing them."""
import json


def candidate_result(conn, project_id: str, run_id: str):
    run = conn.execute(
        "SELECT status,result_json,finished_at FROM transcription_runs WHERE id=? AND project_id=?",
        (run_id, project_id),
    ).fetchone()
    if not run:
        return None
    if run['status'] == 'candidate':
        return json.loads(run['result_json'] or '[]'), False
    # Cancellation can precede the final draft flush; wait for run finalization.
    if run['status'] not in ('cancelled', 'failed') or not run['finished_at']:
        return None
    rows = conn.execute(
        "SELECT start,end,text FROM transcription_segments WHERE run_id=? AND project_id=? ORDER BY idx",
        (run_id, project_id),
    ).fetchall()
    return ([dict(row) for row in rows], True) if rows else None
