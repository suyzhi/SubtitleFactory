import type { ReactNode } from 'react';

interface Props {
  primary: ReactNode;
  blocker: string;
  extra?: ReactNode;
}

export default function StepActions({ primary, blocker, extra }: Props) {
  return (
    <footer className="step-actions">
      {blocker && <p className="step-blocker">{blocker}</p>}
      <div>
        {primary}
        {extra}
      </div>
    </footer>
  );
}
