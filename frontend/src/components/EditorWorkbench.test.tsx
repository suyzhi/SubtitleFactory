import {act,fireEvent,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import EditorWorkbench from './EditorWorkbench';

afterEach(() => {localStorage.clear();vi.restoreAllMocks();vi.unstubAllGlobals();});
it('keeps playback mounted while changing layout, resizing, and restoring saved ratios', async () => {
  let resize=()=>{};let width=1400;
  vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockImplementation(()=>width);
  vi.stubGlobal('ResizeObserver',class {constructor(callback:()=>void){resize=callback;} observe(){} disconnect(){}});
  const user=userEvent.setup();
  const view=render(<EditorWorkbench player={<video data-testid="player"/>} editor={<p>Transcript</p>} timeline={<p>Timeline</p>} toolbar={<button>转写</button>}/>);
  const player=screen.getByTestId('player');
  expect(screen.getByRole('separator')).toHaveAttribute('aria-orientation','vertical');
  await user.selectOptions(screen.getByLabelText('编辑器布局'),'stacked');
  fireEvent.change(screen.getByLabelText('播放区比例'),{target:{value:'60'}});
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow','60');
  expect(localStorage.getItem('subtitle_factory_editor_vertical')).toBe('60');
  await user.selectOptions(screen.getByLabelText('编辑器布局'),'wide');
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow','50');
  width=700;act(()=>resize());
  expect(screen.getByRole('separator')).toHaveAttribute('aria-orientation','horizontal');
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow','60');
  await user.click(screen.getByRole('button',{name:'专注字幕'}));
  expect(screen.getByLabelText('播放区比例')).toBeDisabled();
  await user.click(screen.getByRole('button',{name:'显示播放器'}));
  expect(screen.getByTestId('player')).toBe(player);
  view.unmount();
});
