import { useMemo } from 'react'

type UseFormFieldParams = {
  error?: string
}

export function useFormField({ error }: UseFormFieldParams) {
  const hasError = useMemo(() => Boolean(error), [error])

  return { hasError }
}
