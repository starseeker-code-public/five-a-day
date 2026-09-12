/**
 * Sistema de Soporte - Five a Day
 * Maneja el modal de tickets de soporte y envío al backend
 */

const SupportSystem = {
    // Mapeo de categorías a tipos de backend
    categoryMap: {
        'interfaz': 'frontend',
        'sistema': 'backend',
        'datos': 'database',
        'otro': 'exception'
    },

    categoryDisplayMap: {
        interfaz: 'Interfaz / Problemas visuales',
        sistema: 'Sistema / Errores internos',
        datos: 'Datos / Base de datos',
        otro: 'Otro'
    },
    
    // Estado del modal
    selectedCategory: null,
    
    /**
     * Inicializa el sistema de soporte
     */
    init() {
        // Event listeners para abrir/cerrar modal
        const openBtn = document.getElementById('support-open-btn');
        const closeBtn = document.getElementById('support-close-btn');
        const modal = document.getElementById('support-modal');
        const backBtn = document.getElementById('support-back-btn');
        const sendBtn = document.getElementById('support-send-btn');
        
        if (openBtn) {
            openBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.openModal();
            });
        }
        
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.closeModal());
        }
        
        if (backBtn) {
            backBtn.addEventListener('click', () => this.showCategoryStep());
        }
        
        if (sendBtn) {
            sendBtn.addEventListener('click', () => this.submitTicket());
        }
        
        // Cerrar modal al hacer click fuera
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    this.closeModal();
                }
            });
        }
        
        // Event listeners para categorías
        document.querySelectorAll('.support-category-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const category = btn.dataset.category;
                this.selectCategory(category);
            });
        });
        
        // Cerrar con ESC
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
                this.closeModal();
            }
        });
    },
    
    /**
     * Abre el modal de soporte
     */
    openModal() {
        const modal = document.getElementById('support-modal');
        if (modal) {
            modal.classList.remove('hidden');
            modal.style.display = 'flex';
            this.showCategoryStep();
        }
    },
    
    /**
     * Cierra el modal de soporte
     */
    closeModal() {
        const modal = document.getElementById('support-modal');
        if (modal) {
            modal.classList.add('hidden');
            modal.style.display = 'none';
            this.resetModal();
        }
    },
    
    /**
     * Muestra el paso de selección de categoría
     */
    showCategoryStep() {
        const categoryStep = document.getElementById('support-step-category');
        const messageStep = document.getElementById('support-step-message');
        
        if (categoryStep) {
            categoryStep.classList.remove('hidden');
            categoryStep.style.display = 'block';
        }
        if (messageStep) {
            messageStep.classList.add('hidden');
            messageStep.style.display = 'none';
        }
        
        this.selectedCategory = null;
    },
    
    /**
     * Selecciona una categoría y muestra el paso de mensaje
     */
    selectCategory(category) {
        this.selectedCategory = category;
        
        const categoryStep = document.getElementById('support-step-category');
        const messageStep = document.getElementById('support-step-message');
        const categoryLabel = document.getElementById('support-category-label');
        
        if (categoryStep) {
            categoryStep.classList.add('hidden');
            categoryStep.style.display = 'none';
        }
        if (messageStep) {
            messageStep.classList.remove('hidden');
            messageStep.style.display = 'block';
        }
        
        // Capitalizar primera letra
        if (categoryLabel) categoryLabel.textContent = this.categoryDisplayMap[category] || 'Categoría';
        
        // Focus en el textarea
        const textarea = document.getElementById('support-message');
        if (textarea) {
            textarea.focus();
        }
    },
    
    /**
     * Resetea el modal a su estado inicial
     */
    resetModal() {
        this.selectedCategory = null;
        
        const textarea = document.getElementById('support-message');
        if (textarea) textarea.value = '';
        
        const errorMsg = document.getElementById('support-error');
        if (errorMsg) errorMsg.classList.add('hidden');
        
        const successMsg = document.getElementById('support-success');
        if (successMsg) successMsg.classList.add('hidden');
    },
    
    /**
     * Envía el ticket de soporte al backend
     */
    async submitTicket() {
        const textarea = document.getElementById('support-message');
        const sendBtn = document.getElementById('support-send-btn');
        const errorMsg = document.getElementById('support-error');
        const successMsg = document.getElementById('support-success');
        
        const message = textarea ? textarea.value.trim() : '';
        
        // Validar mensaje
        if (!message) {
            if (errorMsg) {
                errorMsg.textContent = 'Por favor, escribe un mensaje';
                errorMsg.classList.remove('hidden');
            }
            return;
        }
        
        if (message.length < 10) {
            if (errorMsg) {
                errorMsg.textContent = 'El mensaje debe tener al menos 10 caracteres';
                errorMsg.classList.remove('hidden');
            }
            return;
        }
        
        // Deshabilitar botón
        if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.innerHTML = '<span class="material-symbols-outlined animate-spin text-sm">sync</span>';
        }
        
        try {
            // El token CSRF y la comprobación de estado vienen de base.js
            // (window.CSRF_TOKEN / window.apiFetch): input oculto primero, la
            // cookie solo como reserva.
            const data = await window.apiFetch('/api/support/submit/', {
                method: 'POST',
                body: JSON.stringify({
                    category: this.categoryMap[this.selectedCategory] || 'exception',
                    category_display: this.categoryDisplayMap[this.selectedCategory] || this.selectedCategory,
                    message: message,
                    current_url: window.location.pathname
                })
            });

            if (data.success) {
                // Mostrar éxito
                if (successMsg) {
                    successMsg.classList.remove('hidden');
                }
                if (errorMsg) errorMsg.classList.add('hidden');
                
                // Cerrar modal después de 2 segundos
                setTimeout(() => {
                    this.closeModal();
                }, 2000);
            } else {
                throw new Error(data.message || 'Error al enviar el ticket');
            }
        } catch (error) {
            if (errorMsg) {
                // `userMessage` is apiFetch's classification (session expired /
                // server error / network); `message` is the server's own
                // validation text, which is written for humans. Never the raw
                // exception text of anything else.
                errorMsg.textContent = error.userMessage
                    || error.message
                    || window.API_MESSAGES.network;
                errorMsg.classList.remove('hidden');
            }
        } finally {
            // Restaurar botón
            if (sendBtn) {
                sendBtn.disabled = false;
                sendBtn.innerHTML = '<span class="material-symbols-outlined text-sm">send</span>';
            }
        }
    }
    // `getCookie()` lived here and is gone: its only caller was the CSRF
    // fallback in submitTicket, which now goes through window.apiFetch /
    // window.CSRF_TOKEN. `CSRF_COOKIE_HTTPONLY` is True whenever DEBUG=False, so
    // it could not have read the token in testing or production anyway.
};

window.SupportSystem = SupportSystem;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        SupportSystem.init();
    });
} else {
    SupportSystem.init();
}
