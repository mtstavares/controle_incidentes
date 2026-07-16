const NOTIFICATION_DURATION_MS = 3000;
const NOTIFICATION_EXIT_DURATION_MS = 200;

function dismissNotification(notification) {
    if (!notification || notification.dataset.dismissed === 'true') {
        return;
    }

    notification.dataset.dismissed = 'true';
    notification.classList.add('notification--leaving');

    window.setTimeout(function () {
        notification.remove();
    }, NOTIFICATION_EXIT_DURATION_MS);
}

function initializeNotification(notification) {
    if (!notification || notification.dataset.notificationInitialized === 'true') {
        return;
    }

    notification.dataset.notificationInitialized = 'true';
    const closeButton = notification.querySelector('[data-notification-close]');
    const timerId = window.setTimeout(function () {
        dismissNotification(notification);
    }, NOTIFICATION_DURATION_MS);

    if (closeButton) {
        closeButton.addEventListener('click', function () {
            window.clearTimeout(timerId);
            dismissNotification(notification);
        }, { once: true });
    }
}

function initializeAutoDismissNotifications() {
    document.querySelectorAll('[data-app-notification]').forEach(initializeNotification);
}

function showApplicationNotification(message, type = 'info') {
    const container = document.querySelector('[data-notification-container]') || createNotificationContainer();
    const notification = document.createElement('div');
    notification.className = `app-notification app-notification--${type}`;
    notification.dataset.appNotification = '';
    notification.setAttribute('role', type === 'danger' ? 'alert' : 'status');
    notification.setAttribute('aria-live', type === 'danger' ? 'assertive' : 'polite');

    const content = document.createElement('div');
    content.className = 'app-notification__content';
    content.textContent = message;

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'app-notification__close';
    closeButton.dataset.notificationClose = '';
    closeButton.setAttribute('aria-label', 'Fechar notificaÃ§Ã£o');
    closeButton.textContent = 'Ã—';

    notification.appendChild(content);
    notification.appendChild(closeButton);
    container.appendChild(notification);
    initializeNotification(notification);
}

function createNotificationContainer() {
    const container = document.createElement('section');
    container.className = 'app-notification-stack mb-4';
    container.dataset.notificationContainer = '';
    container.setAttribute('aria-label', 'Mensagens do sistema');
    const contentWrap = document.querySelector('.content-wrap') || document.body;
    contentWrap.prepend(container);
    return container;
}

window.showApplicationNotification = showApplicationNotification;

document.addEventListener('DOMContentLoaded', function () {
    const shell = document.getElementById('appShell');
    const sidebarToggle = document.querySelector('[data-sidebar-toggle]');
    const sidebarCloseTargets = document.querySelectorAll('[data-sidebar-close]');

    function setSidebar(open) {
        if (!shell || !sidebarToggle) return;
        shell.classList.toggle('sidebar-open', open);
        document.body.classList.toggle('sidebar-open-body', open);
        sidebarToggle.setAttribute('aria-expanded', String(open));
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function () {
            setSidebar(!shell.classList.contains('sidebar-open'));
        });
    }

    sidebarCloseTargets.forEach(function (target) {
        target.addEventListener('click', function () {
            setSidebar(false);
        });
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            setSidebar(false);
        }
    });

    const commandSelect = document.querySelector('[data-command-select]');
    const btlSelect = document.getElementById('btl_select');
    const cpaInput = document.getElementById('cpa_input');
    const btlInput = document.getElementById('btl_input');
    const unitHelp = document.querySelector('[data-unit-help]');

    if (commandSelect && btlSelect && cpaInput && btlInput) {
        const originalUnitOptions = Array.from(btlSelect.options)
            .filter(function (option) { return option.value; })
            .map(function (option) {
                return {
                    value: option.value,
                    cpa: option.getAttribute('data-cpa') || '',
                    name: option.getAttribute('data-unit-name') || option.textContent.trim(),
                    selected: option.selected
                };
            });

        const buildPlaceholder = function (text) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = text;
            return option;
        };

        const setSelectedUnitName = function () {
            const selectedOption = btlSelect.options[btlSelect.selectedIndex];
            btlInput.value = selectedOption && selectedOption.value
                ? selectedOption.getAttribute('data-unit-name') || selectedOption.textContent.trim()
                : '';
        };

        const syncIncidentUnits = function (options = {}) {
            const commandId = commandSelect.value;
            const selectedCommand = commandSelect.options[commandSelect.selectedIndex];
            const previousUnitValue = options.clearUnit === false ? btlSelect.value : '';
            const seenValues = new Set();

            cpaInput.value = selectedCommand ? selectedCommand.getAttribute('data-command-name') || '' : '';

            const availableUnits = originalUnitOptions.filter(function (unit) {
                if (!commandId || unit.cpa !== commandId || seenValues.has(unit.value)) {
                    return false;
                }
                seenValues.add(unit.value);
                return true;
            });

            btlSelect.replaceChildren(buildPlaceholder(
                commandId
                    ? (availableUnits.length ? 'Selecione o Batalhão/Unidade' : 'Nenhuma unidade cadastrada para este CPA')
                    : 'Selecione primeiro o CPA/Grande Comando'
            ));

            availableUnits.forEach(function (unit) {
                const option = document.createElement('option');
                option.value = unit.value;
                option.textContent = unit.name;
                option.setAttribute('data-cpa', unit.cpa);
                option.setAttribute('data-unit-name', unit.name);
                btlSelect.appendChild(option);
            });

            btlSelect.disabled = !commandId || availableUnits.length === 0;
            btlSelect.value = previousUnitValue && availableUnits.some(function (unit) { return unit.value === previousUnitValue; })
                ? previousUnitValue
                : '';
            setSelectedUnitName();

            if (unitHelp) {
                unitHelp.textContent = commandId
                    ? (availableUnits.length ? 'Selecione uma unidade vinculada ao CPA informado.' : 'Nenhuma unidade cadastrada para este CPA.')
                    : 'Selecione primeiro o CPA/Grande Comando.';
            }
        };

        btlSelect.addEventListener('change', setSelectedUnitName);

        commandSelect.addEventListener('change', function () {
            syncIncidentUnits({ clearUnit: true });
        });

        syncIncidentUnits({ clearUnit: !originalUnitOptions.some(function (unit) { return unit.selected; }) });
    }

    document.querySelectorAll('[data-dashboard-filters]').forEach(function (form) {
        const cpaSelect = form.querySelector('[data-cpa-filter]');
        const btlFilterSelect = form.querySelector('[data-btl-filter]');

        if (!cpaSelect || !btlFilterSelect) return;

        const originalBtlOptions = Array.from(btlFilterSelect.options)
            .filter(function (option) { return option.value && option.value !== 'todos'; })
            .map(function (option) {
                return {
                    value: option.value,
                    label: option.textContent.trim(),
                    cpa: option.getAttribute('data-cpa') || 'todos',
                    selected: option.selected
                };
            });

        const buildTodosOption = function () {
            const option = document.createElement('option');
            option.value = 'todos';
            option.textContent = 'Todos';
            option.setAttribute('data-cpa', 'todos');
            return option;
        };

        const syncBtlOptions = function (options = {}) {
            const cpa = cpaSelect.value || 'todos';
            const previousValue = options.clearBtl ? 'todos' : btlFilterSelect.value;
            const seenValues = new Set();
            const availableOptions = originalBtlOptions.filter(function (option) {
                const isVisible = cpa !== 'todos' && option.cpa === cpa;
                if (!isVisible || seenValues.has(option.value)) {
                    return false;
                }
                seenValues.add(option.value);
                return true;
            });

            btlFilterSelect.replaceChildren(buildTodosOption());
            availableOptions.forEach(function (item) {
                const option = document.createElement('option');
                option.value = item.value;
                option.textContent = item.label;
                option.setAttribute('data-cpa', item.cpa);
                btlFilterSelect.appendChild(option);
            });

            btlFilterSelect.value = availableOptions.some(function (item) { return item.value === previousValue; })
                ? previousValue
                : 'todos';
            btlFilterSelect.disabled = cpa === 'todos' || availableOptions.length === 0;

            if (cpa === 'todos') {
                btlFilterSelect.options[0].textContent = 'Selecione primeiro o CPA';
            } else if (availableOptions.length === 0) {
                btlFilterSelect.options[0].textContent = 'Nenhuma unidade cadastrada para este CPA';
            }
        };

        cpaSelect.addEventListener('change', function () {
            syncBtlOptions({ clearBtl: true });
        });
        syncBtlOptions({ clearBtl: false });
    });

    document.querySelectorAll('[data-toggle-password]').forEach(function (button) {
        button.addEventListener('click', function () {
            const input = document.getElementById(button.getAttribute('data-toggle-password'));
            if (!input) return;
            const visible = input.type === 'text';
            input.type = visible ? 'password' : 'text';
            button.textContent = visible ? 'Mostrar' : 'Ocultar';
            button.setAttribute('aria-pressed', String(!visible));
        });
    });

    document.querySelectorAll('form[data-prevent-duplicate]').forEach(function (form) {
        form.addEventListener('submit', function () {
            const submitters = form.querySelectorAll('button[type="submit"]');
            submitters.forEach(function (button) {
                button.disabled = true;
                if (button.dataset.loadingText) {
                    button.dataset.originalText = button.textContent;
                    button.textContent = button.dataset.loadingText;
                }
            });
        });
    });

    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';
    if (csrfToken) {
        document.querySelectorAll('form[method="POST"], form[method="post"]').forEach(function (form) {
            if (!form.querySelector('input[name="_csrf_token"]')) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = '_csrf_token';
                input.value = csrfToken;
                form.appendChild(input);
            }
        });
    }

    document.querySelectorAll('form[data-live-search]').forEach(function (form) {
        const input = form.querySelector('[data-live-search-input]');
        if (!input) return;
        let timer;
        input.addEventListener('input', function () {
            window.clearTimeout(timer);
            timer = window.setTimeout(function () {
                const url = new URL(form.action, window.location.origin);
                const value = input.value.trim();
                if (value) {
                    url.searchParams.set(input.name, value);
                }
                window.location.href = url.toString();
            }, 350);
        });
    });

    initializeAutoDismissNotifications();

    const incidentSearchInput = document.querySelector('[data-incident-search-input]');
    const incidentResults = document.querySelector('[data-incident-results]');
    const incidentFilterForm = document.querySelector('[data-incident-filter-form]');
    const incidentSearchHidden = document.querySelector('[data-incident-search-hidden]');

    if (incidentSearchInput && incidentResults) {
        let debounceTimer = null;
        let activeController = null;

        const buildSearchUrl = function (pageUrl) {
            const params = new URLSearchParams();
            const term = incidentSearchInput.value.trim();

            if (term) {
                params.set('q', term);
            }

            if (incidentFilterForm) {
                const formData = new FormData(incidentFilterForm);
                ['status_filter', 'sort_by', 'direction'].forEach(function (name) {
                    const value = formData.get(name);
                    if (value) {
                        params.set(name, value);
                    }
                });
            }

            if (pageUrl) {
                const pageParams = new URL(pageUrl, window.location.origin).searchParams;
                const page = pageParams.get('page');
                if (page) {
                    params.set('page', page);
                }
            }

            return `/incidentes/pesquisa?${params.toString()}`;
        };

        const updateBrowserUrl = function () {
            const currentUrl = new URL(window.location.href);
            const term = incidentSearchInput.value.trim();

            if (term) {
                currentUrl.searchParams.set('q', term);
            } else {
                currentUrl.searchParams.delete('q');
            }
            currentUrl.searchParams.delete('page');

            if (incidentFilterForm) {
                const formData = new FormData(incidentFilterForm);
                ['status_filter', 'sort_by', 'direction'].forEach(function (name) {
                    const value = formData.get(name);
                    if (value) {
                        currentUrl.searchParams.set(name, value);
                    }
                });
            }

            window.history.replaceState({}, '', currentUrl);
            if (incidentSearchHidden) {
                incidentSearchHidden.value = term;
            }
        };

        const runSearch = async function (pageUrl) {
            if (activeController) {
                activeController.abort();
            }

            activeController = new AbortController();
            incidentResults.setAttribute('aria-busy', 'true');
            incidentResults.classList.add('is-loading');

            try {
                const response = await fetch(buildSearchUrl(pageUrl), {
                    method: 'GET',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    signal: activeController.signal
                });

                if (!response.ok) {
                    throw new Error(`Falha na pesquisa: ${response.status}`);
                }

                incidentResults.innerHTML = await response.text();
                updateBrowserUrl();
            } catch (error) {
                if (error.name !== 'AbortError') {
                    showApplicationNotification('NÃ£o foi possÃ­vel pesquisar os incidentes.', 'danger');
                }
            } finally {
                incidentResults.setAttribute('aria-busy', 'false');
                incidentResults.classList.remove('is-loading');
            }
        };

        incidentSearchInput.addEventListener('input', function () {
            window.clearTimeout(debounceTimer);
            debounceTimer = window.setTimeout(function () {
                runSearch();
            }, 350);
        });

        incidentResults.addEventListener('click', function (event) {
            const clearButton = event.target.closest('[data-clear-incident-search]');
            if (clearButton) {
                incidentSearchInput.value = '';
                runSearch();
                return;
            }

            const pageLink = event.target.closest('.pagination a.page-link');
            if (pageLink) {
                event.preventDefault();
                runSearch(pageLink.href);
            }
        });
    }

    document.querySelectorAll('[data-rich-editor]').forEach(function (editor) {
        const surface = editor.querySelector('[data-editor-surface]');
        const hiddenInput = document.querySelector('[data-editor-input]');
        if (!surface || !hiddenInput) return;

        const syncEditor = function () {
            hiddenInput.value = surface.innerHTML;
        };

        editor.querySelectorAll('[data-editor-command]').forEach(function (button) {
            button.addEventListener('click', function () {
                surface.focus();
                document.execCommand(button.dataset.editorCommand, false, null);
                syncEditor();
            });
        });

        const fontSelect = editor.querySelector('[data-editor-font]');
        if (fontSelect) {
            fontSelect.addEventListener('change', function () {
                surface.focus();
                document.execCommand('fontName', false, fontSelect.value);
                syncEditor();
            });
        }

        const sizeSelect = editor.querySelector('[data-editor-size]');
        if (sizeSelect) {
            sizeSelect.addEventListener('change', function () {
                surface.focus();
                document.execCommand('fontSize', false, sizeSelect.value);
                syncEditor();
            });
        }

        const foreColor = editor.querySelector('[data-editor-forecolor]');
        if (foreColor) {
            foreColor.addEventListener('input', function () {
                surface.focus();
                document.execCommand('foreColor', false, foreColor.value);
                syncEditor();
            });
        }

        const backColor = editor.querySelector('[data-editor-backcolor]');
        if (backColor) {
            backColor.addEventListener('input', function () {
                surface.focus();
                document.execCommand('hiliteColor', false, backColor.value);
                syncEditor();
            });
        }

        surface.addEventListener('input', syncEditor);
        surface.addEventListener('paste', function (event) {
            const items = event.clipboardData ? Array.from(event.clipboardData.items || []) : [];
            const imageItems = items.filter(function (item) { return item.kind === 'file' && item.type.indexOf('image/') === 0; });
            if (imageItems.length) {
                event.preventDefault();
                const files = imageItems.map(function (item) { return item.getAsFile(); }).filter(Boolean);
                window.addIncidentAttachmentFiles(files);
            }
        });

        const parentForm = surface.closest('form');
        if (parentForm) {
            parentForm.addEventListener('submit', syncEditor);
        }
    });

    const attachmentInput = document.querySelector('[data-attachment-input]');
    const attachmentList = document.querySelector('[data-attachment-list]');
    const dropzones = document.querySelectorAll('[data-attachment-dropzone]');
    const dataTransfer = new DataTransfer();
    const maxFileSize = 20 * 1024 * 1024;
    const allowedExtensions = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.doc', '.docx', '.xls', '.xlsx'];

    function formatFileSize(size) {
        if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
        return `${(size / 1024).toFixed(1)} KB`;
    }

    function renderAttachmentQueue() {
        if (!attachmentList) return;
        attachmentList.textContent = '';
        Array.from(dataTransfer.files).forEach(function (file, index) {
            const extension = file.name.split('.').pop().toLowerCase();
            const item = document.createElement('li');
            item.className = 'attachment-item';

            const icon = document.createElement('span');
            icon.className = `attachment-file-icon attachment-file-icon--${extension}`;
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = extension.toUpperCase();

            const name = document.createElement('span');
            name.className = 'attachment-item__name';
            name.textContent = file.name;

            const meta = document.createElement('span');
            meta.className = 'attachment-item__meta';
            meta.textContent = formatFileSize(file.size);

            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn btn-outline-secondary btn-sm';
            remove.textContent = 'Remover';
            remove.addEventListener('click', function () {
                const nextTransfer = new DataTransfer();
                Array.from(dataTransfer.files).forEach(function (queuedFile, queuedIndex) {
                    if (queuedIndex !== index) {
                        nextTransfer.items.add(queuedFile);
                    }
                });
                dataTransfer.items.clear();
                Array.from(nextTransfer.files).forEach(function (queuedFile) {
                    dataTransfer.items.add(queuedFile);
                });
                attachmentInput.files = dataTransfer.files;
                renderAttachmentQueue();
            });

            item.appendChild(icon);
            item.appendChild(name);
            item.appendChild(meta);
            item.appendChild(remove);
            attachmentList.appendChild(item);
        });
    }

    window.addIncidentAttachmentFiles = function (files) {
        if (!attachmentInput) return;
        Array.from(files).forEach(function (file) {
            const lowerName = file.name.toLowerCase();
            const extension = allowedExtensions.find(function (ext) { return lowerName.endsWith(ext); });
            if (!extension) {
                showApplicationNotification('Tipo de arquivo nÃ£o permitido.', 'danger');
                return;
            }
            if (file.size > maxFileSize) {
                showApplicationNotification('O arquivo excede o limite permitido.', 'danger');
                return;
            }
            dataTransfer.items.add(file);
        });
        attachmentInput.files = dataTransfer.files;
        renderAttachmentQueue();
    };

    if (attachmentInput) {
        attachmentInput.addEventListener('change', function () {
            window.addIncidentAttachmentFiles(attachmentInput.files);
        });
    }

    dropzones.forEach(function (dropzone) {
        ['dragenter', 'dragover'].forEach(function (eventName) {
            dropzone.addEventListener(eventName, function (event) {
                event.preventDefault();
                dropzone.classList.add('is-dragging');
            });
        });
        ['dragleave', 'drop'].forEach(function (eventName) {
            dropzone.addEventListener(eventName, function (event) {
                event.preventDefault();
                dropzone.classList.remove('is-dragging');
            });
        });
        dropzone.addEventListener('drop', function (event) {
            window.addIncidentAttachmentFiles(event.dataTransfer.files);
        });
    });
});
