import type { ReactNode } from 'react'
import styles from './FormField.module.css'
import { useFormField } from './useFormField'

type FormFieldProps = {
  label: string
  htmlFor: string
  error?: string
  children: ReactNode
}

export function FormField({ label, htmlFor, error, children }: FormFieldProps) {
  const { hasError } = useFormField({ error })

  return (
    <label className={styles.field} htmlFor={htmlFor}>
      <span className={styles.label}>{label}</span>
      {children}
      {hasError && (
        <span className={styles.error} role="alert">
          {error}
        </span>
      )}
    </label>
  )
}
