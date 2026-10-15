import { useEffect, useState } from 'react'
import type { PendingApproval } from '../../types/fingerprint'

type UseAccessApprovalDialogParams = {
  approval: PendingApproval
}

/** Counts the approval window down and derives the labels shown in the dialog. */
export function useAccessApprovalDialog({ approval }: UseAccessApprovalDialogParams) {
  const [secondsLeft, setSecondsLeft] = useState(approval.expiresInSeconds)

  // A new scan restarts the countdown from its own expiry window.
  useEffect(() => {
    const deadline = Date.now() + approval.expiresInSeconds * 1000
    const timer = window.setInterval(() => {
      setSecondsLeft(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)))
    }, 250)
    return () => window.clearInterval(timer)
  }, [approval.approvalId, approval.expiresInSeconds])

  const displayName = approval.holderName?.trim() || 'ללא שם'
  const balanceAfterCents = Math.max(0, approval.balanceCents - approval.feeCents)
  const isUrgent = secondsLeft <= 5

  return { secondsLeft, displayName, balanceAfterCents, isUrgent }
}
