#!/usr/bin/env python3
"""Local Qwen/VAD segmentation experiment; official installed model only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
parser=argparse.ArgumentParser();parser.add_argument('--model',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--seconds',type=int);args=parser.parse_args()
if args.seconds is None:
    for seconds in (10,20,30):
        subprocess.run([sys.executable,__file__,'--model',str(args.model),'--output',str(args.output),'--seconds',str(seconds)],check=True)
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix='subtitle-qwen-benchmark-') as temporary:
    os.environ['SUBTITLE_FACTORY_DATA_DIR']=temporary
    # The model is preinstalled; this only prepares the pinned VAD helper.
    from app.models.database import init_db
    from app.utils.task_manager import task_manager
    from app.services.managed_sherpa import _ensure_vad
    from app.services.qwen_mlx import _iter_segments
    init_db(); task=task_manager.create_task(None,'transcribe')
    vad=_ensure_vad(task)
    spec=importlib.util.spec_from_file_location('cpu_benchmark',ROOT/'scripts/benchmark-transcription.py')
    cpu=importlib.util.module_from_spec(spec);spec.loader.exec_module(cpu)
    samples=json.loads((args.output/'samples/manifest.json').read_text())
    results=[]
    for iteration in range(2):
        for sample in samples:
            if sample['name'] not in ('mixed','dialogue','long','continuous'):continue
            start=time.perf_counter();first=None;rows=[]
            for seg in _iter_segments(task,sample['path'],str(args.model.resolve()),sample['language'] or 'auto',vad,sample['duration'],max_speech_seconds=args.seconds):
                if first is None:first=time.perf_counter()-start
                rows.append(dict(start=seg.start,end=seg.end,text=seg.text))
            result=dict(sample=sample['name'],iteration=iteration,max_speech_seconds=args.seconds,total_seconds=time.perf_counter()-start,first_result_seconds=first,process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,segments=rows,**cpu.edits(sample['reference'],' '.join(row['text'] for row in rows)))
            result['out_of_bounds']=sum(row['start']<0 or row['end']>sample['duration']+.1 or row['end']<row['start'] for row in rows)
            result['adjacent_duplicates']=sum(rows[i]['text'].strip()==rows[i-1]['text'].strip() for i in range(1,len(rows)))
            results.append(result)
            (args.output/f'qwen-{args.seconds}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
            print(json.dumps({k:v for k,v in result.items() if k!='segments'},ensure_ascii=False),flush=True)
    task_manager.shutdown()
