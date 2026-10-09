from app.engine.phases import verify_id
from app.engine.state import Phase

HANDLERS = {
    Phase.VERIFY_ID: verify_id.handle,
}
