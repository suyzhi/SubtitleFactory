import {expect,it} from 'vitest';
import {taskIsActive,taskProgressLabel} from './projectState';
import type {TaskStatus} from './types';
it('keeps a cancelled worker active until the final draft flush finishes',()=>{
  const task:TaskStatus={id:'task',project_id:'project',type:'transcribe',step:'transcribing',progress:10,message:'',error:null,created_at:'now',updated_at:'now',status:'cancelled',details:{cancel_requested_at:1,worker_stopped:false}};
  expect(taskIsActive(task)).toBe(true);
  expect(taskProgressLabel(task)).toContain('保存草稿');
  task.details={...task.details,worker_stopped:true};
  expect(taskIsActive(task)).toBe(false);
});
