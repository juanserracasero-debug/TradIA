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
    // Si ya conoces tu URL y anon key de Supabase, puedes reemplazarlas aquí,
    // o dejarlas vacías y la pantalla de login te permitirá ingresarlas con 1 click.
    supabaseUrl: localStorage.getItem("TRADIA_SUPABASE_URL") || "",
    supabaseAnonKey: localStorage.getItem("TRADIA_SUPABASE_ANON_KEY") || ""
};
