import { redirect } from 'next/navigation';

export default function LegacyDiagnosticRedirect() {
  redirect('/audit');
}
