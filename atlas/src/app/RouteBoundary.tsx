import { Component, type ReactNode } from 'react';
import { reportError } from '../errors/UserErrors';
import { PageState } from '../ui/PageState';

/** Keep navigation available if a page render or an old lazy chunk fails. */
export class RouteBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  componentDidCatch(error: Error) { reportError('ui', error); }

  render() {
    return this.state.failed ? (
      <PageState kind="error" title="此頁暫時無法開啟" message="請重新載入，或切換到其他頁面。"
        onRetry={() => window.location.reload()} />
    ) : this.props.children;
  }
}
