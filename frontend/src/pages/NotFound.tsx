import { ArrowLeft } from 'lucide-react';
import { Link } from 'react-router-dom';
import { BrandMark } from '@/components/layout/Sidebar';
import { Button } from '@/components/ui';

export default function NotFound() {
  return (
    <div className="grid-bg flex min-h-screen flex-col items-center justify-center bg-void px-6 text-center">
      <BrandMark className="h-10 w-10" />
      <p className="tnum mt-8 text-6xl font-semibold tracking-tight text-ink">404</p>
      <h1 className="mt-3 text-lg font-medium text-ink">That screen does not exist</h1>
      <p className="mt-2 max-w-md text-sm text-muted">
        The page you asked for is not part of the platform. It may have been renamed, or the link may be stale.
      </p>
      <div className="mt-8 flex gap-3">
        <Link to="/app">
          <Button variant="primary" icon={<ArrowLeft className="h-3.5 w-3.5" />}>
            Back to the command center
          </Button>
        </Link>
        <Link to="/">
          <Button variant="outline">Landing page</Button>
        </Link>
      </div>
    </div>
  );
}
