"""Repeatable storage benchmark on temporary synthetic profiles. No camera.

Optional --baseline points to an archived profile_store.py, not live data.
Both versions use the same warm-cache process and identical synthetic records.
"""
import _bootstrap
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
import numpy as np
from person_store import save_person
from profile_store import ProfileRepository


def measure(repo, identity, iterations):
    for _ in range(10):
        repo.get(identity)
    values = []
    with patch('numpy.load', wraps=np.load) as loads:
        for _ in range(iterations):
            started = time.perf_counter()
            repo.get(identity)
            values.append((time.perf_counter() - started)*1000)
        return dict(median_ms=statistics.median(values),p95_ms=float(np.percentile(values,95)),
                    npz_reads=loads.call_count,iterations=iterations)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--iterations', type=int, default=200)
    args=parser.parse_args()
    with TemporaryDirectory(prefix='vision-benchmark-') as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        for index in range(4):
            vector=np.zeros(128,np.float32);vector[index]=1
            save_person('Person'+str(index),('Software',),'ADMIN' if index==0 else 'USER',
                        [vector]*3,directory,legacy)
        current=ProfileRepository(directory,legacy)
        identity=current.find('Person0').identity
        result={}
        if args.baseline:
            spec=importlib.util.spec_from_file_location('baseline_profiles',args.baseline)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            result['before']=measure(module.ProfileRepository(directory,legacy),identity,args.iterations)
        result['after']=measure(current,identity,args.iterations)
        if 'before' in result:
            result['speedup']=result['before']['median_ms']/result['after']['median_ms']
        print(json.dumps(result,indent=2))


if __name__=='__main__':main()
