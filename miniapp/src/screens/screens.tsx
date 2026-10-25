import { Placeholder } from './Placeholder';

export { FeedScreen } from './Feed';
export { MarketScreen } from './Market';
export { MineScreen } from './Mine';

export { CreateScreen } from './Create';
export { RatingScreen } from './Rating';
export { ProfileScreen } from './Profile';

export function NotFoundScreen() {
  return <Placeholder title="Страница не найдена" cards={0} />;
}
