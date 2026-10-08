import { Component, type ReactNode } from 'react';
import { reportError } from '../errors/UserErrors';
export class PanelBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: '' };
  static getDerivedStateFromError(error: unknown) { return { error: String(error) }; }
  componentDidCatch(error: Error) { reportError('ui', error); }
  render() {
    return this.state.error ? <section role="alert"><p>此 panel 暫時無法使用。圖表可繼續操作。</p><p>{this.state.error}</p><button className="primary-button" onClick={()=>this.setState({error:''})}>重試 Panel</button></section> : this.props.children;
  }
}
