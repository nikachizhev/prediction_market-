import { BalancePill } from '../components/BalancePill';
import { Placeholder } from './Placeholder';

export { FeedScreen } from './Feed';
export { MarketScreen } from './Market';
export { MineScreen } from './Mine';

export function CreateScreen() {
  return <Placeholder title="О чём спрашиваем?" cards={1} />;
}

export function RatingScreen() {
  return <Placeholder title="Рейтинг" cards={4} />;
}

export function ProfileScreen() {
  return <Placeholder title="Профиль" header={<BalancePill />} cards={2} />;
}

export function NotFoundScreen() {
  return <Placeholder title="Страница не найдена" cards={0} />;
}
