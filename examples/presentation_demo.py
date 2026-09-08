"""Build and query the bundled synthetic exports with deterministic rules."""
import json,tempfile
from pathlib import Path
from minekg.ingest import ingest
from minekg import kg
from minekg.ask import ask
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='minekg-demo-') as directory:
    records=Path(directory)/'records.jsonl';db=Path(directory)/'minekg.db'
    imported=ingest(ROOT/'examples/exports',out_path=records)
    kg.reset_db(db)
    linked=kg.link_records(records_path=records,db_path_arg=db,client=None)
    answer=ask('2025 年和小张相关的资金往来合计',db_path=db,client=None)
    print(json.dumps({'records':len(imported),'sources':sorted({r.source for r in imported}),
        'canonical_id':answer.canonical_id,'answer':answer.answer,'amounts':[r['amount'] for r in answer.rows],
        'provenance_rows':len(answer.rows),'ambiguities':len(linked.ambiguities)},ensure_ascii=False,indent=2))
    assert answer.value==500 and len(answer.rows)==2
