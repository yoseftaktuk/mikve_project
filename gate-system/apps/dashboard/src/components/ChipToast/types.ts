export type ChipToastData = {
  kind: 'granted' | 'denied'
  title: string
  message: string
  balanceCents: number | null
  balanceLabel?: string
}
