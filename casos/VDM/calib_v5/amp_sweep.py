import csv, re, subprocess, json, sys, os
import numpy as np
z7={r['id'] for r in csv.DictReader(open('zonas.csv')) if r['group']=='Z_VRP3949706'}
src=open('Mod_VDM_v5_base.inp',encoding='utf-8').read(); nl='\r\n' if '\r\n' in src else '\n'
pat=[float(x) for l in src.split(nl) if l.startswith('PAT-MENESES-DIA') for x in l.split()[1:]]
pat=np.array(pat); m=pat.mean()
def build(a,path):
    newp=m+a*(pat-m)     # misma media, oscilación × a
    out=[];sec=None
    for ln in src.split(nl):
        s=ln.strip()
        if s.startswith('['):
            if sec=='[PATTERNS]':
                out+=['PAT-Z3949706         '+'  '.join(f'{v:.4f}' for v in newp[i:i+6]) for i in range(0,len(newp),6)]
            sec=s.split()[0]
        if sec=='[DEMANDS]' and s and not s.startswith(';'):
            t=ln.split()
            if t[0] in z7 and len(t)>2 and t[2]=='PAT-MENESES-DIA':
                ln=ln.replace('PAT-MENESES-DIA','PAT-Z3949706     ',1)
        out.append(ln)
    open(path,'w',encoding='utf-8',newline='').write(nl.join(out))
cfg=open('config.yaml',encoding='utf-8').read()
for a in [float(x) for x in sys.argv[1:]]:
    tag=f'a{int(a*100):03d}'; inp=f'Mod_VDM_v5_{tag}.inp'; build(a,inp)
    c=cfg.replace('inp: Mod_VDM_v5_base.inp',f'inp: {inp}').replace('output_dir: resultados',f'output_dir: res_{tag}').replace('name: VDM_v5',f'name: VDM_{tag}')
    open(f'config_{tag}.yaml','w',encoding='utf-8').write(c)
    subprocess.run(['python3','-W','ignore','-m','epacal',f'config_{tag}.yaml'],env={**os.environ,'PYTHONPATH':'../../..'},stdout=open(f'run_{tag}.out','w'),stderr=subprocess.STDOUT)
    r=json.load(open(f'res_{tag}/resumen_hidraulica.json'))
    print(tag,[(x['regla'],x['puntos_ok'],x['obs_ok']) for x in r['reglas_calibracion']],flush=True)
