from __future__ import annotations
import hashlib,secrets
from datetime import datetime,timedelta,timezone
from typing import Any
from database import Database,DatabaseConflict,DatabaseError

D=[('ordinary','The Living City'),('coast','The Salt Coast'),('underworld','The Underline'),('mirror','The Mirror District'),('dream','The Dreaming Quarter'),('digital','The Glass Network'),('frontier','The Far Frontier'),('wild','The Wild Country')]
L=[('market','Old Market','ordinary'),('station','Central Station','ordinary'),('rooftops','North Rooftops','ordinary'),('harbor','Night Harbor','coast'),('lighthouse','The Broken Lighthouse','coast'),('tunnels','The Service Tunnels','underworld'),('archive','The Buried Archive','underworld'),('mirror_square','Mirror Square','mirror'),('glass_house','The Glass House','mirror'),('sleeping_hotel','The Sleeping Hotel','dream'),('red_road','The Red Road','frontier'),('orchard','The Silent Orchard','wild'),('relay','Relay Nine','digital'),('server_garden','The Server Garden','digital')]
N=[('mara','Mara Vale','streetwise fixer'),('jonah','Jonah Reed','quiet investigator'),('sana','Sana Iqbal','restless courier'),('elias','Elias North','retired cartographer'),('rhea','Rhea Moss','mechanic with a secret'),('noor','Noor Sen','musician who remembers impossible places'),('keeper','The Keeper','ageless archivist'),('cass','Cass Rowan','charismatic drifter')]
S=[('a stranger leaves a key on your table','social'),('a locked door appears where there was only a wall','mystery'),('someone you trust asks for a dangerous favor','relationship'),('a crowd suddenly runs in the opposite direction','danger'),('a forgotten message arrives from your own future','mystery'),('a small object starts changing when you hold it','wonder'),('two factions offer incompatible promises','faction'),('an injured traveler recognizes you before you meet','relationship'),('the local rules stop making sense','dimension'),('you find a safe place that may not stay safe','survival'),('a quiet conversation reveals a hidden motive','social'),('something valuable is offered for a personal secret','choice'),('a path opens only if you give something up','sacrifice'),('a familiar face appears in a completely wrong place','relationship'),('the world shows you another possible life','dream'),('a celebration hides a private negotiation','social')]
C=[('Approach carefully',{'courage':1,'risk':1}),('Listen before acting',{'insight':1,'trust':1}),('Take the opportunity',{'luck':1,'risk':2}),('Protect the person involved',{'empathy':1,'trust':2}),('Walk away and observe',{'insight':2,'risk':-1}),('Tell the truth',{'honesty':2,'trust':1}),('Keep the secret',{'cunning':1,'trust':-1}),('Make a deal',{'cunning':1,'luck':1}),('Search for another route',{'insight':1,'luck':1})]

class WorldService:
 def __init__(self,db:Database): self.db=db
 @staticmethod
 def now(): return datetime.now(timezone.utc)
 @staticmethod
 def iso(v): return v.astimezone(timezone.utc).isoformat()
 @staticmethod
 def h(*p): return int.from_bytes(hashlib.sha256('|'.join(map(str,p)).encode()).digest()[:8],'big')
 @staticmethod
 def pick(a,n): return a[n%len(a)]
 def pstate(self,uid): return {'status':'alive','lives':3,'health':100,'energy':100,'turns':0,'deaths':0,'cycle':0,'stats':{'courage':0,'insight':0,'luck':0,'empathy':0,'honesty':0,'cunning':0},'inventory':[],'relationships':{},'quests':{},'flags':{},'checkpoint':{'location_id':'market','dimension':'ordinary'},'last_choice':None,'user_id':uid}
 def wstate(self,seed):
  x=self.pick(L,seed);return {'seed':seed,'turn':0,'location_id':x[0],'location_name':x[1],'dimension':x[2],'dimension_name':next(n for k,n in D if k==x[2]),'threat':0,'weather':self.pick(['clear','rain','wind','fog','heat','quiet'],seed//7),'flags':{},'discovered_dimensions':['ordinary'],'discovered_locations':[x[0]],'global_arc':0}
 async def create_world(self,creator_id,chat_id=None,title=None):
  seed=secrets.randbits(62); deadline=self.now()+timedelta(seconds=45); title=(title or 'WHAT HAPPENS?').strip()[:80] or 'WHAT HAPPENS?'
  r=await self.db.request('POST','world_games',params={'select':'*'},json={'creator_id':creator_id,'chat_id':chat_id,'title':title,'status':'waiting','join_deadline':self.iso(deadline),'seed':seed,'world_state':self.wstate(seed),'last_event':{'type':'world_created'},'version':1,'max_players':50},prefer='return=representation')
  if not r: raise DatabaseError('Could not create world game.')
  await self.event(r[0]['id'],creator_id,'world_created',{'seed':seed});return r[0]
 async def get_world(self,gid):
  r=await self.db.request('GET','world_games',params={'id':f'eq.{gid}','limit':'1'})
  if not r: raise ValueError('World game not found.')
  return r[0]
 async def get_group_world(self,cid):
  r=await self.db.request('GET','world_games',params={'chat_id':f'eq.{cid}','status':'in.(waiting,active)','order':'created_at.desc','limit':'1'});return r[0] if r else None
 async def get_player(self,gid,uid):
  r=await self.db.request('GET','world_players',params={'game_id':f'eq.{gid}','telegram_user_id':f'eq.{uid}','limit':'1'});return r[0] if r else None
 async def players(self,gid): return await self.db.request('GET','world_players',params={'game_id':f'eq.{gid}','order':'joined_at.asc'})
 async def join(self,gid,uid,username,display_name):
  g=await self.get_world(gid)
  if g['status'] not in ('waiting','active'): raise ValueError('This world is no longer accepting players.')
  expired=False
  if g['status']=='waiting' and g.get('join_deadline'): expired=self.now()>=datetime.fromisoformat(str(g['join_deadline']).replace('Z','+00:00'))
  old=await self.get_player(gid,uid)
  if old:return old
  if len(await self.players(gid))>=int(g.get('max_players') or 50): raise ValueError('This world is full.')
  r=await self.db.request('POST','world_players',params={'select':'*'},json={'game_id':gid,'telegram_user_id':uid,'username':username or '','display_name':display_name or 'Player','state':self.pstate(uid)},prefer='return=representation')
  if not r: raise DatabaseConflict('Could not join this world.')
  await self.event(gid,uid,'player_joined',{'display_name':display_name or 'Player'})
  if g['status']=='waiting' and expired: await self.start(gid)
  return r[0]
 async def start(self,gid):
  g=await self.get_world(gid)
  if g['status']=='active':return g
  if g['status']!='waiting':return g
  ps=await self.players(gid)
  if not ps:raise ValueError('At least one player must join.')
  r=await self.db.request('PATCH','world_games',params={'id':f'eq.{gid}','status':'eq.waiting'},json={'status':'active','started_at':self.iso(self.now()),'updated_at':self.iso(self.now()),'version':int(g.get('version') or 1)+1},prefer='return=representation')
  g=r[0] if r else await self.get_world(gid);await self.event(gid,None,'world_started',{'players':len(ps)});return g
 async def invite(self,gid,uid):
  t=secrets.token_urlsafe(10).replace('-','_')[:22]
  await self.db.request('POST','world_invites',params={'select':'*'},json={'token':t,'game_id':gid,'created_by':uid,'uses':0,'max_uses':100},prefer='return=representation');return t
 async def consume(self,t,uid,username,name):
  r=await self.db.request('GET','world_invites',params={'token':f'eq.{t}','limit':'1'})
  if not r:raise ValueError('Invite link is invalid or expired.')
  i=r[0]
  if int(i['uses'])>=int(i['max_uses']):raise ValueError('This invite has reached its limit.')
  p=await self.join(str(i['game_id']),uid,username,name);await self.db.request('PATCH','world_invites',params={'token':f'eq.{t}'},json={'uses':int(i['uses'])+1});return {'game':await self.get_world(str(i['game_id'])),'player':p}
 async def snapshot(self,gid,uid):
  g=await self.get_world(gid);p=await self.get_player(gid,uid)
  if not p:raise ValueError('Join this world first.')
  return self.present(g,p)
 def scene(self,g,p):
  w=dict(g.get('world_state') or {});s=dict(p.get('state') or {});seed=int(w.get('seed') or g.get('seed') or 1);turn=int(s.get('turns') or 0);loc=next((x for x in L if x[0]==w.get('location_id')),L[0]);dn=next(n for k,n in D if k==loc[2]);sit,cat=self.pick(S,self.h(seed,turn,p['telegram_user_id'],w.get('global_arc',0)));npc=self.pick(N,self.h(seed,turn,'npc',loc[0]));rel=dict(s.get('relationships') or {});bond=int(rel.get(npc[0],0));text=f"In {loc[1]}, {sit}. {npc[1]} is nearby, watching with the caution of a {npc[2]}. The air feels {w.get('weather','quiet')}, and the boundary of {dn} feels unusually thin."
  if bond>=3:text+=f" {npc[1]} clearly trusts you more than before."
  if bond<=-3:text+=f" {npc[1]} keeps a careful distance from you."
  if turn%7==6 and loc[2]!='ordinary':text+=' For a moment, you glimpse another dimension behind the scene.'
  base=self.h(seed,turn,p['telegram_user_id'],'choices');cs=[]
  for i in range(3):
   label,e=C[(base+i*3)%len(C)];risk=((base>>(i*5))%5)-2;cs.append({'id':f'c{i+1}','label':label,'effects':e,'risk':risk,'npc_id':npc[0]})
  return {'id':f"{loc[0]}:{turn%16}:{cat}:{self.h(seed,turn,loc[0])%9973}",'title':f"{loc[1]} — {sit.capitalize()}",'text':text,'category':cat,'location':loc[1],'dimension':dn,'npc':{'id':npc[0],'name':npc[1],'bond':bond},'choices':cs}
 def present(self,g,p):
  w=g.get('world_state') or {};s=p.get('state') or {}
  return {'game':{'id':g['id'],'title':g['title'],'status':g['status'],'version':g['version'],'players':g.get('max_players',50),'join_deadline':g.get('join_deadline'),'started_at':g.get('started_at')},'player':{'id':p['id'],'display_name':p['display_name'],'username':p['username'],'state':s},'world':{'location':w.get('location_name'),'dimension':w.get('dimension_name'),'turn':s.get('turns',0),'threat':w.get('threat',0),'discoveries':w.get('discovered_dimensions',[])[-8:]},'scene':self.scene(g,p) if g['status']=='active' else None}
 async def choose(self,gid,uid,choice_id,expected):
  g=await self.get_world(gid)
  if g['status']=='waiting':await self.start(gid);g=await self.get_world(gid)
  if g['status']!='active':raise ValueError('This world is not active.')
  p=await self.get_player(gid,uid)
  if not p:raise ValueError('Join this world first.')
  ver=int(g.get('version') or 1)
  if expected!=ver:raise DatabaseConflict('The world changed. Refreshing your scene.')
  sc=self.scene(g,p);ch=next((x for x in sc['choices'] if x['id']==choice_id),None)
  if not ch:raise ValueError('That choice is no longer available.')
  w=dict(g.get('world_state') or {});s=dict(p.get('state') or {});stats=dict(s.get('stats') or {})
  for k,v in ch['effects'].items():
   if k in stats:stats[k]=int(stats.get(k,0))+int(v)
  s['stats']=stats;s['turns']=int(s.get('turns') or 0)+1;s['energy']=max(0,int(s.get('energy',100))-3);rel=dict(s.get('relationships') or {});rel[ch['npc_id']]=int(rel.get(ch['npc_id'],0))+int(ch['effects'].get('trust',0));s['relationships']=rel;s['last_choice']=choice_id;s['health']=max(0,int(s.get('health',100))+(3 if 'protect' in ch['label'].lower() else 0));danger=max(0,int(w.get('threat',0))+int(ch.get('risk',0)));died=self.h(g['seed'],s['turns'],uid,choice_id)%100<min(12+danger*2,35) and ch['risk']>0
  if died:
   s['deaths']=int(s.get('deaths',0))+1;s['lives']=int(s.get('lives',3))-1;s['health']=100;s['energy']=100
   if s['lives']<=0:s['cycle']=int(s.get('cycle',0))+1;s['lives']=3;s.setdefault('flags',{})['major_loss']=True
   loc=self.pick(L,self.h(g['seed'],s['deaths'],uid,'respawn'));w['location_id'],w['location_name'],w['dimension']=loc[0],loc[1],loc[2];w['dimension_name']=next(n for k,n in D if k==loc[2]);w.setdefault('discovered_locations',[]).append(loc[0]);w.setdefault('discovered_dimensions',[])
   if loc[2] not in w['discovered_dimensions']:w['discovered_dimensions'].append(loc[2])
   s['checkpoint']={'location_id':loc[0],'dimension':loc[2]}
  else:
   s['health']=min(100,int(s.get('health',100))+1);w['threat']=min(10,max(0,danger))
   if s['turns']%5==0:
    loc=self.pick(L,self.h(g['seed'],s['turns'],uid,'move'));w['location_id'],w['location_name'],w['dimension']=loc[0],loc[1],loc[2];w['dimension_name']=next(n for k,n in D if k==loc[2]);w.setdefault('discovered_locations',[]).append(loc[0]);w.setdefault('discovered_dimensions',[])
    if loc[2] not in w['discovered_dimensions']:w['discovered_dimensions'].append(loc[2])
  w['turn']=max(int(w.get('turn',0)),s['turns']);w['global_arc']=int(w.get('global_arc',0))+1;event={'type':'choice','user_id':str(uid),'choice_id':choice_id,'scene_id':sc['id'],'died':died,'turn':s['turns']}
  r=await self.db.rpc('commit_world_turn',{'p_game_id':gid,'p_player_id':p['id'],'p_expected_game_version':ver,'p_expected_player_updated_at':p['updated_at'],'p_world_state':w,'p_player_state':s,'p_event':event})
  if not r:raise DatabaseConflict('The world changed. Please try again.')
  return {'snapshot':await self.snapshot(gid,uid),'event':event}
 async def event(self,gid,uid,typ,payload):
  await self.db.request('POST','world_events',json={'game_id':gid,'actor_user_id':uid,'event_type':typ,'payload':payload},prefer='return=minimal')
