#!/usr/bin/env python3
"""Offline, reproducible CPU inference experiment. Never downloads models or uploads audio.
Run with backend/.venv/bin/python; outputs samples, references and raw measured JSON.
Synthetic voices isolate regressions; they do not replace human speech acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time
import wave

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
import numpy as np


def write_audio(path, audio):
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())


def make_samples(root):
    root.mkdir(parents=True,exist_ok=True)
    phrases = [
        ('Samantha', 'Welcome to the subtitle workshop. Import a video, check every sentence, and export the final subtitles.'),
        ('Daniel', 'The meeting begins at nine thirty. Please keep the original recording and review the final version.'),
        ('Tingting', '欢迎使用字幕工厂。请导入视频，检查每一句字幕，然后导出最终文件。'),
    ]
    clips=[]
    for i,(voice,text) in enumerate(phrases):
        aiff=root/f'voice-{i}.aiff'; wav=root/f'voice-{i}.wav'
        if not wav.exists():
            subprocess.run(['say','-v',voice,'-r','155','-o',str(aiff),text],check=True)
            subprocess.run(['ffmpeg','-v','error','-y','-i',str(aiff),'-ar','16000','-ac','1',str(wav)],check=True)
            aiff.unlink()
        with wave.open(str(wav)) as f: clips.append(np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32)/32768)
    silence=np.zeros(16000,dtype=np.float32)
    dialog=np.concatenate([clips[0],silence,clips[1]])
    phrase=phrases[0][1]; second=phrases[1][1]; chinese=phrases[2][1]
    samples=[('short',clips[0],phrase,'en'),('mixed',np.concatenate([clips[0],silence,clips[2]]),phrase+chinese,None),
             ('dialogue',dialog,phrase+' '+second,'en'),('long_silence',np.concatenate([clips[0],np.zeros(45*16000),clips[1]]),phrase+' '+second,'en')]
    long=np.tile(np.concatenate([dialog,np.zeros(3*16000)]),8)
    samples.append(('long',long,(' '+phrase+' '+second)*8,'en'))
    # Deterministic low-volume chord bed, clearly labeled synthetic music.
    t=np.arange(len(dialog))/16000
    chord=sum(np.sin(2*np.pi*freq*t) for freq in [220,277.18,329.63])/3
    samples.append(('music',dialog+.045*chord,phrase+' '+second,'en'))
    manifest=[]
    for name,audio,reference,lang in samples:
        path=root/f'{name}.wav';write_audio(path,audio)
        manifest.append(dict(name=name,path=str(path.resolve()),reference=reference,language=lang,duration=len(audio)/16000))
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    # Shared normal UI import fixture, with actual speech and visible frame.
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0x25314b:s=640x360:r=24','-i',str(root/'short.wav'),'-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(root/'workflow-demo.mp4')],check=True)
    return manifest


def edits(reference,hypothesis):
    # Character-level normalized edit distance supports mixed Chinese/English.
    norm=lambda s: re.sub(r'[^\w]', '',s.lower(),flags=re.UNICODE)
    a,b=norm(reference),norm(hypothesis);prev=list(range(len(b)+1))
    for i,ca in enumerate(a,1):
        row=[i]
        for j,cb in enumerate(b,1):row.append(min(row[-1]+1,prev[j]+1,prev[j-1]+(ca!=cb)))
        prev=row
    return dict(character_errors=prev[-1],reference_characters=len(a),cer=prev[-1]/max(1,len(a)))


def worker(args):
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    manifest=json.loads((args.output/'samples/manifest.json').read_text())
    results=[];start=time.perf_counter()
    model=WhisperModel(str(args.model),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
    load=time.perf_counter()-start
    batched=BatchedInferencePipeline(model) if args.mode=='batch4' else None
    for iteration in range(2):
        for sample in manifest:
            if args.mode!='baseline' and sample['name'] not in ('short','mixed','dialogue','music'):continue
            started=time.perf_counter();first=None;rows=[]
            opts=dict(language=sample['language'],beam_size=1 if args.mode=='beam1' else 5,word_timestamps=True,vad_filter=True)
            if batched: gen,info=batched.transcribe(sample['path'],batch_size=4,**opts)
            else: gen,info=model.transcribe(sample['path'],**opts)
            for seg in gen:
                if first is None:first=time.perf_counter()-started
                rows.append(dict(start=seg.start,end=seg.end,text=seg.text,words=[dict(start=w.start,end=w.end,word=w.word) for w in seg.words or []]))
            hypothesis=' '.join(r['text'] for r in rows)
            entry=dict(sample=sample['name'],iteration=iteration,mode=args.mode,duration=sample['duration'],model_load_seconds=load,first_result_seconds=first,total_seconds=time.perf_counter()-started,process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,segments=rows,**edits(sample['reference'],hypothesis))
            entry['out_of_bounds']=sum(r['start']<0 or r['end']>sample['duration']+.1 or r['end']<r['start'] for r in rows)
            entry['adjacent_duplicates']=sum(rows[i]['text'].strip()==rows[i-1]['text'].strip() for i in range(1,len(rows)))
            results.append(entry)
            (args.output/f'{args.mode}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=lambda value:value.item()))
            print(json.dumps({k:v for k,v in entry.items() if k!='segments'},ensure_ascii=False,default=lambda value:value.item()),flush=True)
    del model

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--mode',choices=['baseline','beam1','batch4']);parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    if args.mode:worker(args)
    else:
        make_samples(args.output/'samples')
        if not args.prepare_only:
            for mode in ['baseline','beam1','batch4']:
                subprocess.run([sys.executable,__file__,'--model',str(args.model),'--output',str(args.output),'--mode',mode],check=True)
