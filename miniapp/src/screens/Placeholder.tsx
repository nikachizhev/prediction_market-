import type { ReactNode } from 'react';
import { CardSkeleton } from '../components/Skeleton';

/** Temporary screen body: title and loading skeletons. Replaced by real screens in later tasks. */
export function Placeholder({ title, header, cards = 3 }: { title: string; header?: ReactNode; cards?: number }) {
  return (
    <div className="screen">
      <header className="screen-header">
        <h1 className="screen-title">{title}</h1>
        {header}
      </header>
      <div className="screen-body">
        {Array.from({ length: cards }, (_, i) => (
          <CardSkeleton key={i} />
        ))}
      </div>
    </div>
  );
}
