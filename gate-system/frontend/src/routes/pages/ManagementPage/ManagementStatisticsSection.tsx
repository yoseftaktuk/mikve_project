import { formatMoney } from '../../../app/money'
import { statusCardStyles } from '../../../components/StatusCard'
import type {
  DailyAccessPoint,
  ManagementStatistics,
  StatisticsRangeKey,
} from '../../../types/managementStats'
import styles from './ManagementPage.module.css'

const RANGE_OPTIONS: { key: StatisticsRangeKey; label: string }[] = [
  { key: 'today', label: 'היום' },
  { key: '7d', label: '7 ימים' },
  { key: 'month', label: 'חודש נוכחי' },
]

const DENIAL_REASON_LABELS: Record<string, string> = {
  unknown_fingerprint: 'אצבע לא מזוהה',
  member_disabled: 'משתמש חסום',
  insufficient_balance: 'יתרה לא מספיקה',
  subscription_inactive: 'מנוי לא פעיל',
  daily_limit_reached: 'מכסת כניסה יומית',
  subscription_unavailable: 'מנוי לא זמין',
  ledger_unavailable: 'שירות יתרות לא זמין',
  door_exhausted: 'כשל בפתיחת דלת',
  door_failed: 'כשל בפתיחת דלת',
}

type ManagementStatisticsSectionProps = {
  range: StatisticsRangeKey
  onRangeChange: (range: StatisticsRangeKey) => void
  stats: ManagementStatistics | null
  loading: boolean
  error: string | null
  onRefresh: () => void
}

/** Hebrew statistics panel: KPIs, daily chart, denials, payments, operational alerts. */
export function ManagementStatisticsSection({
  range,
  onRangeChange,
  stats,
  loading,
  error,
  onRefresh,
}: ManagementStatisticsSectionProps) {
  const operational = stats?.operational
  const hasOperationalIssues =
    !!operational &&
    (operational.pending_card_topups > 0 ||
      operational.crediting_card_topups > 0 ||
      operational.failed_access_attempts > 0 ||
      operational.door_operation_failures > 0)

  const kpis = stats?.kpis
  const approvalPct = kpis ? Math.round(kpis.approval_rate * 100) : 0

  return (
    <div aria-labelledby="management-stats-title">
      <div className={styles.usersHeader}>
        <h2 id="management-stats-title" className={statusCardStyles.sectionTitle}>
          סטטיסטיקה
        </h2>
        <button
          type="button"
          className={styles.refreshButton}
          disabled={loading}
          onClick={() => void onRefresh()}
        >
          {loading ? 'טוען…' : 'רענן'}
        </button>
      </div>

      <div className={styles.rangeToggle} role="group" aria-label="טווח תאריכים">
        {RANGE_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={range === opt.key ? styles.rangeButtonActive : styles.rangeButton}
            aria-pressed={range === opt.key}
            disabled={loading}
            onClick={() => onRangeChange(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {error && <p className={styles.error}>{error}</p>}

      {loading && !stats && <p className={statusCardStyles.hint}>טוען סטטיסטיקה…</p>}

      {stats && kpis && (
        <>
          <div className={styles.kpiGrid}>
            <Kpi label="כניסות שאושרו" value={String(kpis.granted_count)} />
            <Kpi label="כניסות שנדחו" value={String(kpis.denied_count)} />
            <Kpi label="שיעור אישור" value={`${approvalPct}%`} />
            <Kpi label="דמי כניסה (מאושרות)" value={formatMoney(kpis.entrance_fee_total_cents)} />
            <Kpi
              label="טעינות כרטיס (מס׳)"
              value={String(kpis.paid_card_topup_count)}
              hint="כספים שנטענו — לא הכנסה חשבונאית"
            />
            <Kpi label="טעינות כרטיס (סה״כ)" value={formatMoney(kpis.paid_card_topup_total_cents)} />
            <Kpi label="מנויים חודשיים (מס׳)" value={String(kpis.paid_monthly_subscription_count)} />
            <Kpi
              label="מנויים חודשיים (סה״כ)"
              value={formatMoney(kpis.paid_monthly_subscription_total_cents)}
            />
            <Kpi label="רשומים פעילים" value={String(kpis.enabled_member_count)} />
            <Kpi label="רשומים חסומים" value={String(kpis.disabled_member_count)} />
            <Kpi label="סה״כ יתרות נוכחיות" value={formatMoney(kpis.total_member_balances_cents)} />
          </div>

          <h3 className={styles.statsSubheading}>כניסות לפי יום</h3>
          <DailyAccessChart points={stats.daily_access} />

          <div className={styles.statsColumns}>
            <div>
              <h3 className={styles.statsSubheading}>סיבות דחייה</h3>
              {stats.denial_reasons.length === 0 ? (
                <p className={statusCardStyles.hint}>אין דחיות בטווח שנבחר.</p>
              ) : (
                <ul className={styles.breakdownList}>
                  {stats.denial_reasons.map((row) => (
                    <li key={row.reason}>
                      <span>{DENIAL_REASON_LABELS[row.reason] ?? row.reason}</span>
                      <strong>{row.count}</strong>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <h3 className={styles.statsSubheading}>שיטת כניסה (הושלמו)</h3>
              <ul className={styles.breakdownList}>
                <li>
                  <span>מזומן</span>
                  <strong>{stats.access_methods.cash}</strong>
                </li>
                <li>
                  <span>אצבע / יתרה</span>
                  <strong>{stats.access_methods.member}</strong>
                </li>
                <li>
                  <span>מנוי</span>
                  <strong>{stats.access_methods.subscription}</strong>
                </li>
              </ul>
              <p className={statusCardStyles.hint}>
                לפי עסקאות סאגה שהושלמו בלבד; דחיות לפני סאגה לא נכללות כאן.
              </p>
            </div>

            <div>
              <h3 className={styles.statsSubheading}>תשלומים ומנויים</h3>
              <ul className={styles.breakdownList}>
                <li>
                  <span>טעינות כרטיס ששולמו</span>
                  <strong>
                    {kpis.paid_card_topup_count} · {formatMoney(kpis.paid_card_topup_total_cents)}
                  </strong>
                </li>
                <li>
                  <span>מנויים ששולמו</span>
                  <strong>
                    {kpis.paid_monthly_subscription_count} ·{' '}
                    {formatMoney(kpis.paid_monthly_subscription_total_cents)}
                  </strong>
                </li>
                <li>
                  <span>דמי כניסה (מאושרות)</span>
                  <strong>{formatMoney(kpis.entrance_fee_total_cents)}</strong>
                </li>
              </ul>
            </div>
          </div>

          {hasOperationalIssues && operational && (
            <div className={styles.operationalAlerts} role="status">
              <h3 className={styles.statsSubheading}>התראות תפעול</h3>
              <ul className={styles.breakdownList}>
                {operational.pending_card_topups > 0 && (
                  <li>
                    <span>טעינות ממתינות</span>
                    <strong>{operational.pending_card_topups}</strong>
                  </li>
                )}
                {operational.crediting_card_topups > 0 && (
                  <li>
                    <span>טעינות בזיכוי</span>
                    <strong>{operational.crediting_card_topups}</strong>
                  </li>
                )}
                {operational.failed_access_attempts > 0 && (
                  <li>
                    <span>ניסיונות כניסה כושלים</span>
                    <strong>{operational.failed_access_attempts}</strong>
                  </li>
                )}
                {operational.door_operation_failures > 0 && (
                  <li>
                    <span>כשלי פתיחת דלת בטווח</span>
                    <strong>{operational.door_operation_failures}</strong>
                  </li>
                )}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className={styles.kpiItem}>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={styles.kpiValue}>{value}</div>
      {hint && <div className={styles.kpiHint}>{hint}</div>}
    </div>
  )
}

function DailyAccessChart({ points }: { points: DailyAccessPoint[] }) {
  if (points.length === 0) {
    return <p className={statusCardStyles.hint}>אין נתוני כניסה בטווח שנבחר.</p>
  }

  const maxTotal = Math.max(
    1,
    ...points.map((p) => p.granted_count + p.denied_count),
  )
  const width = Math.max(280, points.length * 48)
  const height = 140
  const padBottom = 28
  const padTop = 12
  const chartH = height - padBottom - padTop
  const barSlot = width / points.length
  const barWidth = Math.min(28, barSlot * 0.55)

  return (
    <div className={styles.chartWrap}>
      <svg
        className={styles.chartSvg}
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="גרף כניסות מאושרות ונדחות לפי יום"
      >
        {points.map((point, index) => {
          const x = index * barSlot + (barSlot - barWidth) / 2
          const grantedH = (point.granted_count / maxTotal) * chartH
          const deniedH = (point.denied_count / maxTotal) * chartH
          const grantedY = padTop + chartH - grantedH
          const deniedY = grantedY - deniedH
          const dayLabel = point.date.slice(5) // MM-DD
          return (
            <g key={point.date}>
              {deniedH > 0 && (
                <rect
                  x={x}
                  y={deniedY}
                  width={barWidth}
                  height={deniedH}
                  fill="#ef4444"
                >
                  <title>{`${point.date}: נדחו ${point.denied_count}`}</title>
                </rect>
              )}
              {grantedH > 0 && (
                <rect
                  x={x}
                  y={grantedY}
                  width={barWidth}
                  height={grantedH}
                  fill="#16a34a"
                >
                  <title>{`${point.date}: אושרו ${point.granted_count}`}</title>
                </rect>
              )}
              {grantedH === 0 && deniedH === 0 && (
                <rect
                  x={x}
                  y={padTop + chartH - 2}
                  width={barWidth}
                  height={2}
                  fill="currentColor"
                  opacity={0.25}
                />
              )}
              <text
                x={x + barWidth / 2}
                y={height - 8}
                textAnchor="middle"
                className={styles.chartAxisLabel}
              >
                {dayLabel}
              </text>
            </g>
          )
        })}
      </svg>
      <div className={styles.chartLegend}>
        <span>
          <i className={styles.legendGranted} aria-hidden /> אושר
        </span>
        <span>
          <i className={styles.legendDenied} aria-hidden /> נדחה
        </span>
      </div>
    </div>
  )
}
