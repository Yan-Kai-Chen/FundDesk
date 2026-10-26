import test from 'node:test';
import assert from 'node:assert/strict';
import {restoredPage,rememberPage} from '../../src/frontend/src/sessionView.ts';

test('reopening restores a mode-specific page and respects explicit fund links',()=>{
  const data=new Map(),storage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v)};
  rememberPage(storage,'real','records');rememberPage(storage,'demo','research');
  assert.equal(restoredPage(storage,'real'),'records');
  assert.equal(restoredPage(storage,'demo'),'research');
  assert.equal(restoredPage(storage,'real',true),'workspace');
  data.set('funding-page-real','deleted-page');assert.equal(restoredPage(storage,'real'),'workspace');
});
