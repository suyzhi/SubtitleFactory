import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect,it,vi} from 'vitest';
import TranscriptionCandidates from './TranscriptionCandidates';
import * as api from '../api/backend';

vi.mock('../api/backend', () => ({listTranscriptionCandidates:vi.fn(),getTranscriptionCandidate:vi.fn(),acceptTranscriptionCandidate:vi.fn()}));

it('retries failed candidate details without requiring a different selection', async () => {
  vi.mocked(api.listTranscriptionCandidates).mockResolvedValue({candidates:[{id:'run',model:'small',segments_count:1,finished_at:'now'}]} as Awaited<ReturnType<typeof api.listTranscriptionCandidates>>);
  vi.mocked(api.getTranscriptionCandidate).mockRejectedValueOnce(new Error('连接中断')).mockResolvedValueOnce({segments:[{start:0,end:1,text:'Recovered candidate'}]} as Awaited<ReturnType<typeof api.getTranscriptionCandidate>>);
  const user=userEvent.setup();
  render(<TranscriptionCandidates projectId="project" revision={1} segments={[]} hasDraft={false} onAccept={vi.fn()} onSeek={vi.fn()}/>);
  await screen.findByRole('option',{name:/small/});
  await user.selectOptions(screen.getByRole('combobox'),'run');
  expect(await screen.findByRole('alert')).toHaveTextContent('连接中断');
  await user.click(screen.getByRole('button',{name:'重新加载候选'}));
  expect(await screen.findByText('Recovered candidate')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'使用候选结果替换当前字幕'})).toBeEnabled();
});
