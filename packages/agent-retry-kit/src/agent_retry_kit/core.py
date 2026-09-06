from dataclasses import dataclass
RETRYABLE={"timeout","rate_limit","transient"}
@dataclass(frozen=True)
class RetryDecision:
    retry: bool; delay_ms: int; reason: str
def decide(attempt:int,error:str,*,max_attempts:int=3,base_ms:int=100,cap_ms:int=5000)->RetryDecision:
    if not 0<=attempt<=100 or not 1<=max_attempts<=100: raise ValueError("bounded attempts required")
    if error not in RETRYABLE: return RetryDecision(False,0,"non_retryable")
    if attempt>=max_attempts: return RetryDecision(False,0,"exhausted")
    return RetryDecision(True,min(cap_ms,base_ms*(2**attempt)),"retryable")
def run(data): return decide(**data).__dict__

