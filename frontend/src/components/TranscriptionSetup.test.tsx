import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect,it,vi} from 'vitest';
import TranscriptionSetup from './TranscriptionSetup';

it('lists an installed GPU variant without silently selecting its runtime', async () => {
  const onModel=vi.fn();const onRuntime=vi.fn();const user=userEvent.setup();
  render(<TranscriptionSetup models={[
    {id:'small',name:'Small',ready:true,download_required:false,languages:['multilingual'],runtimes:[{id:'cpu',name:'CPU',available:true,model_ready:true}]},
    {id:'qwen',name:'Qwen GPU',ready:false,download_required:true,languages:['multilingual'],runtimes:[{id:'cpu',name:'CPU',available:true,model_ready:false},{id:'mlx',name:'Apple GPU',available:true,model_ready:true}]},
    {id:'missing',name:'Missing model',ready:false,download_required:true,languages:['multilingual']},
  ]} model="small" recommended="small" language="en" runtime="cpu" onModel={onModel} onRuntime={onRuntime} onLanguage={vi.fn()}/>);
  await user.click(screen.getByRole('combobox',{name:'转写模型'}));
  expect(screen.queryByRole('option',{name:/Missing model/})).not.toBeInTheDocument();
  await user.click(screen.getByRole('option',{name:/Qwen GPU 已安装/}));
  expect(onModel).toHaveBeenCalledWith('qwen');
  expect(onRuntime).not.toHaveBeenCalled();
});
