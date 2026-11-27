"""Bounded local decision adapter. No platform transport or workspace access."""
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID
from types import SimpleNamespace
from importlib.metadata import version

package=Path(sys.argv[1]).resolve()
manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
source=json.loads(Path(__file__).with_name('source.json').read_text(encoding='utf-8'))
assert manifest['sourceArchiveSha256']==source['sourceArchiveSha256']
assert manifest['files']==source['files']
assert version('chipzen-bot')=='0.4.0'
for name,digest in manifest['files'].items():
    assert name in {'hu_bot/'+n for n in ['__init__.py','poker.py','v2_contracts.py','v2_policy.py','v2_runtime.py']}
    assert hashlib.sha256((package/name).read_bytes()).hexdigest()==digest
sys.path.insert(0,str(package))
from hu_bot.v2_runtime import V2Runtime

KEYS=set('contract requestId handId sessionId revision sequence playerId originPlayers actor dealerSeat street ownCards board stacks bets active pot smallBlind bigBlind handIndex history legal seed budgetMs'.split())
DECK={r+s for r in '23456789TJQKA' for s in 'cdhs'}
PHASES=['preflop','flop','turn','river']
instances={}

def validate(r):
    if not isinstance(r,dict) or set(r)!=KEYS or r['contract']!='riverline-bot/1':
        raise ValueError('strict request fields differ')
    for k in ['revision','sequence','actor','dealerSeat','street','pot','smallBlind','bigBlind','handIndex','budgetMs','originPlayers']:
        if type(r[k]) is not int or r[k]<0:raise ValueError('invalid integer '+k)
    for k in ['requestId','handId','sessionId']:
        if not isinstance(r[k],str) or str(UUID(r[k]))!=r[k]:raise ValueError('invalid UUID '+k)
    if not isinstance(r['playerId'],str) or not 1<=len(r['playerId'])<=100 or not isinstance(r['seed'],str) or not 1<=len(r['seed'])<=128:raise ValueError('invalid identity/seed')
    if r['sequence']>300 or r['handIndex']<1 or r['pot']>1000000:raise ValueError('invalid sequence/amount')
    if r['originPlayers']!=2 or r['actor'] not in (0,1) or r['dealerSeat'] not in (0,1):raise ValueError('only native HU supported')
    if r['bigBlind']!=100 or r['smallBlind']!=50 or not 1<=r['budgetMs']<=1000 or not 0<=r['street']<=3:raise ValueError('rules/budget unsupported')
    cards=r['ownCards']+r['board']
    if len(r['ownCards'])!=2 or len(r['board'])!=[0,3,4,5][r['street']] or len(set(cards))!=len(cards) or any(c not in DECK for c in cards):raise ValueError('invalid visible cards')
    for k in ['stacks','bets']:
        if len(r[k])!=2 or any(type(v)is not int or not 0<=v<=1000000 for v in r[k]):raise ValueError('invalid public amounts')
    if len(r['active'])!=2 or any(type(v)is not bool for v in r['active']):raise ValueError('invalid player state')
    if len(r['history'])>300 or not 1<=len(r['legal'])<=4:raise ValueError('invalid history/legal size')
    for e in r['history']:
        if set(e)-{'actor','kind','amount','street'} or not {'actor','kind','street'}<=set(e) or type(e['actor'])is not int or type(e['street'])is not int or e['actor']not in(0,1) or e['kind']not in('fold','check','call','raise') or e['street']not in(0,1,2,3):raise ValueError('invalid history')
        if 'amount'in e and(type(e['amount'])is not int or not 0<=e['amount']<=1000000):raise ValueError('invalid history amount')
    for e in r['legal']:
        if set(e)-{'kind','amount','min','max'} or e.get('kind')not in('fold','check','call','raise'):raise ValueError('invalid legal menu')
        if any(type(v)is not int or not 0<=v<=1000000 for k,v in e.items()if k!='kind'):raise ValueError('invalid legal amount')
        if e['kind']=='raise':
            if 'min'not in e or 'max'not in e or e['min']>e['max']:raise ValueError('invalid raise limits')
        elif 'min'in e or 'max'in e or(e['kind']=='call'and'amount'not in e):raise ValueError('invalid legal bounds')
    if len({e['kind']for e in r['legal']})!=len(r['legal']):raise ValueError('duplicate legal action')

def decide(r):
    validate(r)
    key=(r['sessionId'],r['playerId'])
    runtime=instances.get(key)
    if runtime is None:
        if len(instances)>=12:
            _,old=instances.popitem()
            old._policy_executor.shutdown(wait=False,cancel_futures=True)
        runtime=V2Runtime(seed=int(hashlib.sha256(r['seed'].encode()).hexdigest()[:8],16))
        runtime.reset(r['sessionId'],r['bigBlind'],2*r['budgetMs'])
        instances[key]=runtime
    runtime.timeout_ms=2*r['budgetMs']
    runtime.set_hand_start_stacks(r['handIndex'],[10000,10000])
    history=[{'phase':'preflop','seat':r['dealerSeat'],'action':'post_small_blind','amount':50},
             {'phase':'preflop','seat':1-r['dealerSeat'],'action':'post_big_blind','amount':100}]
    history += [{'phase':PHASES[e['street']],'seat':e['actor'],'action':e['kind'],'amount':e.get('amount',0)} for e in r['history']]
    actor=r['actor']
    call=next((e for e in r['legal']if e['kind']=='call'),None)
    raise_=next((e for e in r['legal']if e['kind']=='raise'),None)
    raw=SimpleNamespace(action_history=history,phase=PHASES[r['street']],your_seat=actor,dealer_seat=r['dealerSeat'],
        hand_number=r['handIndex'],round_id=r['handId'],hole_cards=r['ownCards'],board=r['board'],pot=r['pot'],
        your_stack=r['stacks'][actor],opponent_stacks=[r['stacks'][1-actor]],to_call=call.get('amount',0)if call else 0,
        min_raise=raise_['min']if raise_ else 0,max_raise=raise_['max']if raise_ else 0,valid_actions=[a['kind']for a in r['legal']])
    action=runtime.decide(raw).to_dict()
    kind=action['action']
    if kind=='all_in':kind='raise';action['amount']=raise_['max']
    output={'kind':kind}
    if kind=='raise':output['amount']=action['amount']
    trace=runtime.last_trace
    return {'requestId':r['requestId'],'handId':r['handId'],'revision':r['revision'],'sequence':r['sequence'],
        'source':'hu-v2.2.2-experimental','action':output,'elapsedMs':trace['resource']['return_elapsed_ms'],
        'fallback':trace.get('fallback_reason')}

print(json.dumps({'ready':True,'contract':'riverline-bot/1','source':'hu-v2.2.2-experimental'}),flush=True)
while True:
    line=sys.stdin.readline(65538)
    if not line:break
    if len(line.encode())>65536:raise ValueError('request exceeds 64KiB')
    request={}
    try:
        request=json.loads(line)
        print(json.dumps({'ok':True,'reply':decide(request)},separators=(',',':')),flush=True)
    except Exception as exc:
        print(json.dumps({'ok':False,'requestId':request.get('requestId'),'error':str(exc)[:200]}),flush=True)
