import { Component, type ReactNode } from 'react';
import { reportError } from '../errors/UserErrors';
import { PageState } from '../ui/PageState';

/** Keep navigation available if a page render or an old lazy chunk fails. */
export class RouteBoundary extends Component<{ children: ReactNode }, { failed: boolean; reason: string }> {
  state = { failed: false, reason: '' };

  static getDerivedStateFromError(error: unknown) {
    const chunk = error instanceof Error && /import|module|chunk|fetch/i.test(error.message);
    return { failed: true, reason: chunk
      ? '頁面檔案載入失敗，可能是連線中斷或網站剛更新。請重新載入。'
      : '此頁面執行時發生錯誤。請重新載入，或切換到其他頁面。' };
  }

  componentDidCatch(error: Error) { reportError('ui', error); }

  render() {
    return this.state.failed ? (
      <PageState kind="error" title="此頁暫時無法開啟" message={this.state.reason}
        onRetry={() => window.location.reload()} />
    ) : this.props.children;
  }
}
