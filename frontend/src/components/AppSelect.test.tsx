import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import AppSelect, {AppSelectThemeContext} from './AppSelect';

const OPTIONS = [
  { value: 'zh', label: '中文', description: 'Chinese' },
  { value: 'en', label: 'English', description: 'English' },
  { value: 'ja', label: '日本語', description: 'Japanese' },
];

describe('AppSelect', () => {
  it('renders the listbox in a body portal and chooses with the keyboard', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<AppSelect value="zh" onChange={onChange} options={OPTIONS} label="目标语言" />);

    const trigger = screen.getByRole('combobox', { name: '目标语言' });
    await user.click(trigger);

    const listbox = screen.getByRole('listbox', { name: '目标语言选项' });
    expect(listbox.parentElement?.parentElement).toBe(document.body);
    expect(listbox.parentElement).toContainElement(screen.getByRole('combobox'));
    expect(listbox).toHaveClass('app-select-popover');
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-activedescendant');

    await user.keyboard('{ArrowDown}{Enter}');
    expect(onChange).toHaveBeenCalledWith('en');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveFocus();
  });

  it('closes on Escape and restores focus', async () => {
    const user = userEvent.setup();
    render(<AppSelect value="zh" onChange={() => undefined} options={OPTIONS} label="源语言" searchable />);

    const input = screen.getByRole('combobox', { name: '源语言' });
    await user.click(input);
    await user.type(screen.getByRole('combobox'), 'English');
    expect(screen.getByRole('option', { name: /English\s+English/ })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveFocus();
  });

  it('closes once after a mouse selection and does not reopen on restored focus', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<AppSelect value="zh" onChange={onChange} options={OPTIONS} label="源语言" searchable />);

    const input = screen.getByRole('combobox', { name: '源语言' });
    input.focus();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    await user.click(input);
    await user.click(screen.getByRole('option', { name: /English\s+English/ }));

    expect(onChange).toHaveBeenCalledWith('en');
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
    expect(screen.getByRole('combobox')).toHaveFocus();
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-expanded', 'false');
  });
  it('dismisses on wrapping label text without reopening', async () => {
    const user = userEvent.setup();
    render(<label><span>双语顺序说明</span><AppSelect value="zh" onChange={() => {}} options={OPTIONS} label="双语顺序" /></label>);
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByText('双语顺序说明'));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('inherits the theme from a settings dialog outside the app root', async () => {
    const user = userEvent.setup();
    render(<div className="settings-backdrop theme-dark"><AppSelect value="zh" onChange={() => {}} options={OPTIONS} label="语言" /></div>);
    await user.click(screen.getByRole('combobox'));
    expect(screen.getByRole('listbox')).toHaveClass('theme-dark');
  });

  it('never opens from label text, before or after dismissing the menu', async () => {
    const user=userEvent.setup();
    render(<label><span>说明与空白区域</span><AppSelect value="zh" onChange={()=>{}} options={OPTIONS} label="顺序" /></label>);
    for(let i=0;i<2;i++) {
      await user.click(screen.getByText('说明与空白区域'));
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    }
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByText('说明与空白区域'));
    await user.click(screen.getByText('说明与空白区域'));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('presses the trigger and menu as one surface and toggles closed', async () => {
    const user=userEvent.setup();
    render(<AppSelect value="zh" onChange={()=>{}} options={OPTIONS} label="语言" />);
    await user.click(screen.getByRole('combobox'));
    const surface=screen.getByRole('listbox').parentElement!;
    await user.pointer({target:screen.getByRole('combobox'),keys:'[MouseLeft>]'});
    expect(surface).toHaveClass('pressed');
    await user.pointer({keys:'[/MouseLeft]'});
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

});

it('updates closed and open controls in the same render as a theme change',async()=>{
 const user=userEvent.setup();
 const control=(theme:'light'|'dark')=><AppSelectThemeContext.Provider value={theme}><AppSelect value="zh" onChange={()=>{}} options={OPTIONS} label="界面密度"/></AppSelectThemeContext.Provider>;
 const {rerender}=render(control('dark'));
 expect(screen.getByRole('combobox').parentElement).toHaveClass('theme-dark');
 rerender(control('light'));
 expect(screen.getByRole('combobox').parentElement).toHaveClass('theme-light');
 await user.click(screen.getByRole('combobox'));
 rerender(control('dark'));
 expect(screen.getByRole('listbox')).toHaveClass('theme-dark');
 expect(screen.getByRole('combobox').parentElement).toHaveClass('theme-dark');
});
