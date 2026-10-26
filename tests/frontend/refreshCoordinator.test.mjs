import test from 'node:test';
import assert from 'node:assert/strict';
import { RefreshCoordinator } from '../../src/frontend/src/refreshCoordinator.ts';

const tick = async () => { for (let i=0;i<8;i++) await Promise.resolve(); };
function fixture() {
  let now=0, next=0, allowed=true;
  const timers=new Map(), requests=[], commits=[], errors=[];
  const coordinator=new RefreshCoordinator({
    now:()=>now, setTimer:(fn,ms)=>{const id=++next;timers.set(id,{at:now+ms,fn});return id;},clearTimer:id=>timers.delete(id),
    canCommit:()=>allowed, fetch:signal=>new Promise((resolve,reject)=>requests.push({resolve,reject,signal})),
    commit:x=>commits.push(x),error:e=>errors.push(e.message),
  });
  return {coordinator,requests,commits,errors,timers,setAllowed:v=>{allowed=v;},
    async advance(ms) {
      const target=now+ms;
      for(let i=0;i<100;i++) {
        const next=[...timers].filter(([,t])=>t.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
        if(!next){now=target;return;}
        now=next[1].at;timers.delete(next[0]);next[1].fn();await tick();
      }
      throw Error('timer loop');
    },
  };
}
async function initial(f) {
  const done=f.coordinator.refreshNow();f.requests[0].resolve('initial');await done;return f;
}
test('focus bursts do not create reads before the 60-second cache interval',async()=>{
  const f=await initial(fixture());
  for(let i=0;i<10;i++){f.coordinator.activity();f.coordinator.wake();await f.advance(1100);}
  assert.equal(f.requests.length,1);assert.equal(f.timers.size,1);f.coordinator.dispose();
});
test('a held drag spanning multiple intervals waits until release plus one quiet second',async()=>{
  const f=await initial(fixture());f.coordinator.hold(true);await f.advance(180000);
  assert.equal(f.requests.length,1);f.coordinator.hold(false);await f.advance(999);
  assert.equal(f.requests.length,1);await f.advance(1);assert.equal(f.requests.length,2);
  f.requests[1].resolve('new');await tick();assert.deepEqual(f.commits,['initial','new']);f.coordinator.dispose();
});
test('scroll/resize bursts postpone a due request until the gesture settles',async()=>{
  const f=await initial(fixture());await f.advance(59900);
  for(let i=0;i<20;i++){f.coordinator.activity();await f.advance(100);}
  assert.equal(f.requests.length,1);await f.advance(900);assert.equal(f.requests.length,2);f.coordinator.dispose();
});
test('in-flight data is parked while editing, then committed once without another fetch',async()=>{
  const f=await initial(fixture());await f.advance(60000);f.setAllowed(false);f.coordinator.activity();
  f.requests[1].resolve('new');await tick();await f.advance(10000);assert.deepEqual(f.commits,['initial']);
  f.setAllowed(true);f.coordinator.activity();f.coordinator.wake();await f.advance(1000);
  assert.deepEqual(f.commits,['initial','new']);assert.equal(f.requests.length,2);f.coordinator.dispose();
});
test('a hidden window pauses polling and an old parked result is re-read on resume',async()=>{
  const f=await initial(fixture());await f.advance(60000);f.setAllowed(false);
  f.requests[1].resolve('old');await tick();await f.advance(180000);assert.equal(f.requests.length,2);
  f.setAllowed(true);f.coordinator.activity();f.coordinator.wake();await f.advance(1000);
  assert.equal(f.requests.length,3);assert.deepEqual(f.commits,['initial']);
  f.requests[2].resolve('fresh');await tick();assert.deepEqual(f.commits,['initial','fresh']);f.coordinator.dispose();
});
test('manual refresh has no interval throttle and replaces an old request even if abort is ignored',async()=>{
  const f=await initial(fixture());await f.advance(60000);f.coordinator.hold(true);
  const done=f.coordinator.refreshNow();assert.equal(f.requests[1].signal.aborted,true);
  f.requests[2].resolve('manual');await done;f.requests[1].resolve('obsolete');await tick();
  const again=f.coordinator.refreshNow();f.requests[3].resolve('again');await again;
  assert.deepEqual(f.commits,['initial','manual','again']);f.coordinator.dispose();
});
test('one slow automatic read cannot overlap through focus and timer bursts',async()=>{
  const f=await initial(fixture());await f.advance(60000);
  for(let i=0;i<10;i++){f.coordinator.activity();f.coordinator.wake();await f.advance(1000);}
  assert.equal(f.requests.length,2);assert.equal(f.timers.size,0);f.coordinator.dispose();
});
test('disposed account controller cannot commit stale data/errors or leave timers',async()=>{
  const f=await initial(fixture());await f.advance(60000);f.coordinator.dispose();
  f.requests[1].resolve('wrong account');await tick();await f.advance(120000);
  assert.equal(f.requests[1].signal.aborted,true);assert.deepEqual(f.commits,['initial']);assert.equal(f.timers.size,0);
});
test('automatic errors wait for interaction to end; explicit errors reach the caller',async()=>{
  const f=await initial(fixture());await f.advance(60000);f.coordinator.hold(true);
  f.requests[1].reject(Error('offline'));await tick();assert.deepEqual(f.errors,[]);
  f.coordinator.hold(false);await f.advance(1000);assert.deepEqual(f.errors,['offline']);
  const done=f.coordinator.refreshNow();f.requests[2].reject(Error('manual offline'));
  await assert.rejects(done,/manual offline/);assert.deepEqual(f.errors,['offline','manual offline']);f.coordinator.dispose();
});
