import { Placeholder } from './Placeholder';

export { FeedScreen } from './Feed';
export { MarketScreen } from './Market';
export { MineScreen } from './Mine';

export { CreateScreen } from './Create';
export { CreatedScreen } from './Created';
export { RatingScreen } from './Rating';
export { ProfileScreen } from './Profile';
export { NotificationsScreen } from './Notifications';
export { ChatScreen } from './Chat';

export function NotFoundScreen() {
  return <Placeholder title="Страница не найдена" cards={0} />;
}
