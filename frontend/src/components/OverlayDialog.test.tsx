import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {it,expect,vi} from 'vitest';
import OverlayDialog from './OverlayDialog';
it('dismisses only on the backdrop or Escape',async()=>{
 const user=userEvent.setup(),close=vi.fn();
 render(<OverlayDialog label="任务中心" onClose={close}><button>关闭</button><input aria-label="输入"/></OverlayDialog>);
 await user.click(screen.getByRole('textbox'));
 expect(close).not.toHaveBeenCalled();
 await user.keyboard('{Escape}');
 expect(close).toHaveBeenCalledTimes(1);
 await user.click(screen.getByRole('dialog').parentElement!);
 expect(close).toHaveBeenCalledTimes(2);
});
