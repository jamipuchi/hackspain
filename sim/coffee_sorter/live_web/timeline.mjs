function scoreEpoch(state) {
  return state?.score_epoch_id || state?.reject_policy?.score_epoch_id || null;
}

export function samePresentationTimeline(left, right) {
  return left?.session_id === right?.session_id && scoreEpoch(left) === scoreEpoch(right);
}
