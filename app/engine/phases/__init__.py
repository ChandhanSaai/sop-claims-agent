from app.engine.phases import post_process, process_case, resolve_intent, verify_id
from app.engine.state import Phase

HANDLERS = {
    Phase.VERIFY_ID: verify_id.handle,
    Phase.RESOLVE_INTENT: resolve_intent.handle,
    Phase.PROCESS_CASE: process_case.handle,
    Phase.POST_PROCESS: post_process.handle,
}
