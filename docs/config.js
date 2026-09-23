/**
 * TradIA - Configuración del Cliente Web (GitHub Pages -> Supabase)
 * 
 * SEGURIDAD:
 * - Utiliza EXCLUSIVAMENTE la clave 'anon' (pública).
 * - Toda la seguridad de lectura y escritura está blindada por Row Level Security (RLS) en Supabase.
 * - NUNCA utilices aquí la clave 'service_role'.
 * - Puedes editar las constantes por defecto aquí o introducirlas en la pantalla de login (se guardan en localStorage).
 */
window.TRADIA_CONFIG = {
    // Configuración directa de conexión con Supabase (clave pública 'anon')
    supabaseUrl: "https://hqpigajjqdolpfbkwlcn.supabase.co",
    supabaseAnonKey: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhxcGlnYWpqcWRvbHBmYmt3bGNuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTAxODc2OTgsImV4cCI6MjEwNTc2MzY5OH0.gK8ffOtc7frvpc76N_EwrbLlX0ctihmPMWde_WD1MZU"
};

