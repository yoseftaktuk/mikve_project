import { Link } from 'react-router-dom'
import { useBackLink } from './useBackLink'

type BackLinkProps = {
  to: string
  children: React.ReactNode
  inline?: boolean
}

export function BackLink({ to, children, inline }: BackLinkProps) {
  const { className } = useBackLink({ inline })

  return (
    <Link className={className} to={to}>
      {children}
    </Link>
  )
}
