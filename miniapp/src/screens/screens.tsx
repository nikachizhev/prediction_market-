import { useParams } from 'react-router-dom';
import { BalancePill } from '../components/BalancePill';
import { Placeholder } from './Placeholder';

export function FeedScreen() {
  return <Placeholder title="Лента" header={<BalancePill />} />;
}

export function MineScreen() {
  return <Placeholder title="Мои" />;
}

export function CreateScreen() {
  return <Placeholder title="О чём спрашиваем?" cards={1} />;
}

export function RatingScreen() {
  return <Placeholder title="Рейтинг" cards={4} />;
}

export function ProfileScreen() {
  return <Placeholder title="Профиль" header={<BalancePill />} cards={2} />;
}

export function MarketScreen() {
  const { id } = useParams();
  return <Placeholder title={`Рынок №${id ?? ''}`} cards={2} />;
}

export function NotFoundScreen() {
  return <Placeholder title="Страница не найдена" cards={0} />;
}
