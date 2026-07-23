import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import AppSelect from './AppSelect';

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
    expect(listbox.parentElement).toBe(document.body);
    expect(listbox).toHaveClass('app-select-popover');
    expect(trigger).toHaveAttribute('aria-activedescendant');

    await user.keyboard('{ArrowDown}{Enter}');
    expect(onChange).toHaveBeenCalledWith('en');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('closes on Escape and restores focus', async () => {
    const user = userEvent.setup();
    render(<AppSelect value="zh" onChange={() => undefined} options={OPTIONS} label="源语言" searchable />);

    const input = screen.getByRole('combobox', { name: '源语言' });
    await user.click(input);
    await user.type(input, 'English');
    expect(screen.getByRole('option', { name: 'EnglishEnglish' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(input).toHaveFocus();
  });

  it('closes once after a mouse selection and does not reopen on restored focus', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<AppSelect value="zh" onChange={onChange} options={OPTIONS} label="源语言" searchable />);

    const input = screen.getByRole('combobox', { name: '源语言' });
    input.focus();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    await user.click(input);
    await user.click(screen.getByRole('option', { name: 'EnglishEnglish' }));

    expect(onChange).toHaveBeenCalledWith('en');
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute('aria-expanded', 'false');
  });
});
