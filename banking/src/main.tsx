import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './lib/namespace'
import { DirectionProvider } from './components/ui/direction.tsx'
import { initializeDateLocale } from './lib/date'
import { initializeCalendarLocale } from './lib/calendar-locale'

const frappe = window.frappe
frappe._messages ??= {}

async function renderApp(direction: 'ltr' | 'rtl') {
  await Promise.all([
    frappe._translations_loaded,
    initializeDateLocale(),
    initializeCalendarLocale(),
  ])
  const { default: App } = await import('./App.tsx')
  createRoot(document.getElementById('root') as HTMLElement).render(
    <StrictMode>
      <DirectionProvider dir={direction}>
        <App />
      </DirectionProvider>
    </StrictMode>,
  )
}

if (import.meta.env.DEV) {
  fetch('/api/method/erpnext.www.banking.get_context_for_dev', {
    method: 'POST',
  }).then(response => response.json()).then(async (values) => {
    frappe.boot = JSON.parse(values.message.boot)
    frappe._translations_loaded = fetch(
      `/api/method/frappe.translate.get_boot_translations?v=${frappe.boot.translations_version}&lang=${frappe.boot.lang}`,
      { credentials: 'same-origin' },
    ).then(response => response.json()).then(data => {
      frappe._messages = data.message || {}
    }).catch(() => {})
    document.dir = values.message.layout_direction
    frappe.model.sync(frappe.boot.docs)
    await renderApp(values.message.layout_direction)
  })
} else {
  frappe.model.sync(frappe.boot.docs)
  void renderApp(frappe.boot.layout_direction ?? 'ltr')
}
