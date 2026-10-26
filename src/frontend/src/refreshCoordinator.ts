type Timer = ReturnType<typeof setTimeout>;
type Options<T> = {
  fetch: (signal: AbortSignal) => Promise<T>;
  commit: (value: T) => void;
  error: (error: Error) => void;
  canCommit: () => boolean;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => Timer;
  clearTimer?: (timer: Timer) => void;
  pollMs?: number;
  quietMs?: number;
};

// One request and one deferred result, regardless of how many focus/scroll
// events arrive. Data may load in the background but never replaces a gesture.
export class RefreshCoordinator<T> {
  private options: Options<T>;
  private now: () => number;
  private setTimer: (fn: () => void, ms: number) => Timer;
  private clearTimer: (timer: Timer) => void;
  private pollMs: number;
  private quietMs: number;
  private timer: Timer | null = null;
  private active: AbortController | null = null;
  private pending: { value?: T; error?: Error; at: number } | null = null;
  private generation = 0;
  private dueAt = 0;
  private lastActivity = -Infinity;
  private held = false;
  private disposed = false;

  constructor(options: Options<T>) {
    this.options = options;
    this.now = options.now || (() => performance.now());
    this.setTimer = options.setTimer || ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = options.clearTimer || (timer => clearTimeout(timer));
    this.pollMs = options.pollMs ?? 60000;
    this.quietMs = options.quietMs ?? 1000;
  }

  activity() {
    this.lastActivity = this.now();
    this.schedule();
  }

  hold(value: boolean) {
    this.held = value;
    this.activity();
  }

  wake() {
    if (this.disposed || this.active) return;
    const quiet = !this.held && this.options.canCommit() && this.now() - this.lastActivity >= this.quietMs;
    if (quiet && this.pending) {
      const pending = this.pending;
      this.pending = null;
      // A result parked throughout a long edit is re-read before display.
      if (this.now() - pending.at < this.pollMs) {
        if (pending.error) this.options.error(pending.error);
        else this.options.commit(pending.value as T);
        this.schedule();
        return;
      }
    }
    if (quiet && this.now() >= this.dueAt) {
      void this.run(false);
      return;
    }
    this.schedule();
  }

  refreshNow() {
    if (this.disposed) return Promise.resolve();
    this.active?.abort();
    this.pending = null;
    return this.run(true);
  }

  dispose() {
    this.disposed = true;
    this.generation++;
    this.active?.abort();
    this.active = null;
    this.pending = null;
    if (this.timer != null) this.clearTimer(this.timer);
    this.timer = null;
  }

  private schedule() {
    if (this.timer != null) this.clearTimer(this.timer);
    this.timer = null;
    if (this.disposed || this.active) return;
    const waiting = !!this.pending || this.now() >= this.dueAt;
    const blocked = this.held || !this.options.canCommit();
    const delay = waiting
      ? blocked ? this.pollMs : Math.max(0, this.lastActivity + this.quietMs - this.now())
      : this.dueAt - this.now();
    this.timer = this.setTimer(() => { this.timer = null; this.wake(); }, delay);
  }

  private async run(manual: boolean) {
    const generation = ++this.generation;
    const controller = new AbortController();
    this.active = controller;
    if (this.timer != null) this.clearTimer(this.timer);
    this.timer = null;
    this.dueAt = this.now() + this.pollMs;
    try {
      const value = await this.options.fetch(controller.signal);
      if (this.disposed || generation !== this.generation) return;
      if (manual) this.options.commit(value);
      else this.pending = { value, at: this.now() };
    } catch (error) {
      if (this.disposed || generation !== this.generation || controller.signal.aborted) return;
      if (manual) {
        this.options.error(error as Error);
        throw error;
      }
      this.pending = { error: error as Error, at: this.now() };
    } finally {
      if (!this.disposed && generation === this.generation) {
        this.active = null;
        this.wake();
      }
    }
  }
}
