import { useParams } from 'react-router-dom';
import { Placeholder } from './Placeholder';

export { FeedScreen } from './Feed';

export function MineScreen() {
  return <Placeholder title="Мои" />;
}

export { CreateScreen } from './Create';
export { RatingScreen } from './Rating';
export { ProfileScreen } from './Profile';

export function MarketScreen() {
  const { id } = useParams();
  return <Placeholder title={`Рынок №${id ?? ''}`} cards={2} />;
}

export function NotFoundScreen() {
  return <Placeholder title="Страница не найдена" cards={0} />;
}
