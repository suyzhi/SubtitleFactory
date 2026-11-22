// @vitest-environment jsdom
import {describe,it,expect} from 'vitest';
import {publishTask,nextTaskRequest} from './taskStore';
import type {TaskStatus} from './types';
describe('canonical task state',()=>{
  it('does not let a stale poll reverse a cancellation',()=>{
    const stale=nextTaskRequest();
    const task={id:'task-store-order',status:'cancelled',type:'workflow',progress:0,details:{},logs:[]} as unknown as TaskStatus;
    const latest=publishTask(task);
    expect(publishTask({...task,status:'running'},stale)).toBe(latest);
  });
});
