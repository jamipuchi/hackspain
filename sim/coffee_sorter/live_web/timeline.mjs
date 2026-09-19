function scoreEpoch(state) {
  return state?.score_epoch_id || state?.reject_policy?.score_epoch_id || null;
}

export function samePresentationTimeline(left, right) {
  return left?.session_id === right?.session_id && scoreEpoch(left) === scoreEpoch(right);
}

export class PolicyIntentBuffer {
  constructor() {
    this.catalog = new Set();
    this.intents = new Map();
    this.expiredEpoch = null;
  }

  setCatalog(names) {
    this.catalog = new Set(names);
    for (const name of this.intents.keys()) {
      if (!this.catalog.has(name)) this.intents.delete(name);
    }
  }

  record(name, reject) {
    if (!this.catalog.has(name)) return false;
    this.intents.set(name, Boolean(reject));
    return true;
  }

  clear() {
    this.intents.clear();
    this.expiredEpoch = null;
  }

  take() {
    const taken = new Map(this.intents);
    this.intents.clear();
    return taken;
  }

  requeue(olderIntents) {
    const newerIntents = this.intents;
    this.intents = new Map();
    for (const [name, reject] of olderIntents) this.record(name, reject);
    for (const [name, reject] of newerIntents) this.record(name, reject);
  }

  apply(baseClasses, intents = this.intents) {
    const target = new Set(baseClasses);
    for (const [name, reject] of intents) {
      if (reject) target.add(name);
      else target.delete(name);
    }
    return target;
  }

  waitForFreshEpoch(expiredEpoch) {
    this.expiredEpoch = expiredEpoch;
  }

  canDispatch(commandEpoch) {
    return Boolean(commandEpoch) && commandEpoch !== this.expiredEpoch;
  }

  acceptEpoch(commandEpoch) {
    if (this.canDispatch(commandEpoch)) this.expiredEpoch = null;
  }

  get size() {
    return this.intents.size;
  }
}
