/**
 * 设置页面 JavaScript
 * 使用 utils.js 中的工具库
 */

// DOM 元素
const elements = {
    tabs: document.querySelectorAll('.tab-btn'),
    tabContents: document.querySelectorAll('.tab-content'),
    registrationForm: document.getElementById('registration-settings-form'),
    backupBtn: document.getElementById('backup-btn'),
    cleanupBtn: document.getElementById('cleanup-btn'),
    addEmailServiceBtn: document.getElementById('add-email-service-btn'),
    addServiceModal: document.getElementById('add-service-modal'),
    addServiceForm: document.getElementById('add-service-form'),
    closeServiceModal: document.getElementById('close-service-modal'),
    cancelAddService: document.getElementById('cancel-add-service'),
    serviceType: document.getElementById('service-type'),
    serviceConfigFields: document.getElementById('service-config-fields'),
    emailServicesTable: document.getElementById('email-services-table'),
    // Outlook 导入
    toggleImportBtn: document.getElementById('toggle-import-btn'),
    outlookImportBody: document.getElementById('outlook-import-body'),
    outlookImportBtn: document.getElementById('outlook-import-btn'),
    clearImportBtn: document.getElementById('clear-import-btn'),
    outlookImportData: document.getElementById('outlook-import-data'),
    importResult: document.getElementById('import-result'),
    // 批量操作
    selectAllServices: document.getElementById('select-all-services'),
    // 代理列表
    proxiesTable: document.getElementById('proxies-table'),
    addProxyBtn: document.getElementById('add-proxy-btn'),
    testAllProxiesBtn: document.getElementById('test-all-proxies-btn'),
    addProxyModal: document.getElementById('add-proxy-modal'),
    proxyItemForm: document.getElementById('proxy-item-form'),
    closeProxyModal: document.getElementById('close-proxy-modal'),
    cancelProxyBtn: document.getElementById('cancel-proxy-btn'),
    proxyModalTitle: document.getElementById('proxy-modal-title'),
    // 动态代理设置
    dynamicProxyForm: document.getElementById('dynamic-proxy-form'),
    proxyPreferenceForm: document.getElementById('proxy-preference-form'),
    testProxyPreferenceBtn: document.getElementById('test-proxy-preference-btn'),
    proxyOperationSettingsForm: document.getElementById('proxy-operation-settings-form'),
    testDynamicProxyBtn: document.getElementById('test-dynamic-proxy-btn'),
    // CPA 服务管理
    addCpaServiceBtn: document.getElementById('add-cpa-service-btn'),
    cpaServicesTable: document.getElementById('cpa-services-table'),
    cpaServiceEditModal: document.getElementById('cpa-service-edit-modal'),
    closeCpaServiceModal: document.getElementById('close-cpa-service-modal'),
    cancelCpaServiceBtn: document.getElementById('cancel-cpa-service-btn'),
    cpaServiceForm: document.getElementById('cpa-service-form'),
    cpaServiceModalTitle: document.getElementById('cpa-service-modal-title'),
    testCpaServiceBtn: document.getElementById('test-cpa-service-btn'),
    // Sub2API 服务管理
    addSub2ApiServiceBtn: document.getElementById('add-sub2api-service-btn'),
    sub2ApiServicesTable: document.getElementById('sub2api-services-table'),
    sub2ApiServiceEditModal: document.getElementById('sub2api-service-edit-modal'),
    closeSub2ApiServiceModal: document.getElementById('close-sub2api-service-modal'),
    cancelSub2ApiServiceBtn: document.getElementById('cancel-sub2api-service-btn'),
    sub2ApiServiceForm: document.getElementById('sub2api-service-form'),
    sub2ApiServiceModalTitle: document.getElementById('sub2api-service-modal-title'),
    testSub2ApiServiceBtn: document.getElementById('test-sub2api-service-btn'),
    // Team Manager 服务管理
    addTmServiceBtn: document.getElementById('add-tm-service-btn'),
    tmServicesTable: document.getElementById('tm-services-table'),
    tmServiceEditModal: document.getElementById('tm-service-edit-modal'),
    closeTmServiceModal: document.getElementById('close-tm-service-modal'),
    cancelTmServiceBtn: document.getElementById('cancel-tm-service-btn'),
    tmServiceForm: document.getElementById('tm-service-form'),
    tmServiceModalTitle: document.getElementById('tm-service-modal-title'),
    testTmServiceBtn: document.getElementById('test-tm-service-btn'),
    // 验证码设置
    emailCodeForm: document.getElementById('email-code-form'),
    // HeroSMS 设置
    smsSettingsForm: document.getElementById('sms-settings-form'),
    testSmsBtn: document.getElementById('test-sms-btn'),
    // Outlook 设置
    outlookSettingsForm: document.getElementById('outlook-settings-form'),
    // Web UI 访问控制
    webuiSettingsForm: document.getElementById('webui-settings-form')
};

// 选中的服务 ID
let selectedServiceIds = new Set();
let herosmsCountries = [];
let smsInspectorState = { topCountries: [], operators: [], quotes: [] };
let dynamicProxyProfiles = {};
let seekproxyGeoCache = { countries: [], states: {}, cities: {} };
let lastDynamicProxyProfileKey = null;
const smsProviderUiConfig = {
    herosms: {
        label: 'HeroSMS',
        serviceExample: 'dr',
        serviceHint: 'HeroSMS 使用服务代码，例如 <code>dr</code> 表示 OpenAI。',
        enabledHint: '关闭后，遇到 add-phone 只记录日志并跳过，不会调用 HeroSMS。',
        apiKeyHint: 'HeroSMS 密码框不回显真实 Key；留空表示保持数据库中已保存的 HeroSMS Key 不变。',
        docsTitle: 'HeroSMS 配置说明',
        docsDesc: 'HeroSMS 主要使用数字国家码与服务代码，当前实现支持余额、国家、服务、价格、推荐国家、运营商与接码轮询。',
        docsFields: '关键字段:service=服务代码，country=数字国家码，min_price=下限，maxPrice=上限。系统会先按 min/max 形成允许区间，再在区间内应用最低价优先与价格放宽倍数。',
        docsLinks: '<a href="https://hero-sms.com/cn/api" target="_blank" rel="noreferrer">官方文档</a>',
        countryLabel: '国家代码',
        countryHint: '上方可搜索选择国家；下方保存实际 HeroSMS 国家代码。',
        countrySearchPlaceholder: '搜索国家名称或代码，例如 菲律宾 / Philippines / 4',
        countryKeyLabel: '国家 slug/key',
        countryKeyHint: 'HeroSMS 不使用国家 slug/key，可留空。',
        maxPriceLabel: '最大单价',
        maxPriceHint: 'HeroSMS 支持按最大单价筛选；填 <code>-1</code> 表示不限制。',
        operatorHint: 'HeroSMS 支持按运营商取号；留空表示不指定运营商。',
        proxyLabel: 'HeroSMS 专用代理（可选）',
        inspectorHint: 'HeroSMS 支持推荐国家、服务列表、运营商、运营商报价等查询。',
        providerQuotesLabel: 'HeroSMS 当前没有 provider 级报价接口。',
        walletSupported: false,
        walletHint: 'HeroSMS 当前未实现静态钱包接口。',
    },
    smsbower: {
        label: 'SMSBower',
        serviceExample: 'dr',
        serviceHint: 'SMSBower 使用服务代码；可配合 providerIds / exceptProviderIds / minPrice / phoneException 定向取号。',
        enabledHint: '关闭后，遇到 add-phone 只记录日志并跳过，不会调用 SMSBower。',
        apiKeyHint: 'SMSBower 密码框不回显真实 Key；留空表示保持数据库中已保存的 SMSBower Key 不变。',
        docsTitle: 'SMSBower 配置说明',
        docsDesc: 'SMSBower 客户端文档显示取号主接口为 getNumber，同时支持 JSON 扩展返回、Provider 报价与静态钱包接口。',
        docsFields: '关键字段:service、country、minPrice、maxPrice、providerIds、exceptProviderIds、phoneException。规则顺序为：先按 min/max 过滤 provider，再在允许区间里应用最低价优先与价格放宽倍数。',
        docsLinks: '<a href="https://smsbower.app/api/?page=client" target="_blank" rel="noreferrer">官方文档</a>',
        countryLabel: '国家代码',
        countryHint: '上方可搜索选择国家；下方保存实际 SMSBower 国家代码。',
        countrySearchPlaceholder: '搜索国家名称或代码，例如 Russia / 151',
        countryKeyLabel: '国家 slug/key',
        countryKeyHint: 'SMSBower 不使用国家 slug/key，可留空。',
        maxPriceLabel: '最大单价',
        maxPriceHint: 'SMSBower 同时支持 maxPrice 与 minPrice；填 <code>-1</code> 表示不限制。',
        operatorHint: 'SMSBower 文档未公开运营商查询接口；通常留空，必要时使用 activationOperator 兼容参数。',
        proxyLabel: 'SMSBower 专用代理（可选）',
        inspectorHint: 'SMSBower 支持服务列表、推荐国家、Provider 级报价、静态钱包。运营商相关能力取决于服务端兼容情况。',
        providerQuotesLabel: 'SMSBower 支持按 providerIds / exceptProviderIds 进一步限制取号来源。',
        walletSupported: true,
        walletHint: 'SMSBower 支持静态钱包查询（例如 usdt/tron）。',
    },
    '5sim': {
        label: '5SIM',
        serviceExample: 'openai',
        serviceHint: '5SIM 使用产品名（product），例如 <code>openai</code>、<code>facebook</code>、<code>telegram</code>。',
        enabledHint: '关闭后，遇到 add-phone 只记录日志并跳过，不会调用 5SIM。',
        apiKeyHint: '5SIM 密码框不回显真实 Key；留空表示保持数据库中已保存的 5SIM Key 不变。',
        docsTitle: '5SIM 配置说明',
        docsDesc: '5SIM 用户接口使用 REST 路径，不使用数字国家码，而是使用国家 slug 与产品名。',
        docsFields: '关键字段:country_key=国家 slug，service=产品名，min_price=下限，maxPrice=上限。可参考 5SIM 设置页固定格式：countries 页提供国家 slug，products 页提供产品名。',
        docsLinks: '<a href="https://5sim.net/zh/docs#user" target="_blank" rel="noreferrer">官方文档</a> · <a href="https://5sim.net/zh/settings/countries" target="_blank" rel="noreferrer">国家格式</a> · <a href="https://5sim.net/zh/settings/products" target="_blank" rel="noreferrer">产品格式</a> · <a href="https://5sim.net/zh/settings/operators" target="_blank" rel="noreferrer">运营商页</a>',
        countryLabel: '国家代码',
        countryHint: '5SIM 不使用数字国家码，下方输入框仅保留兼容，不参与实际取号。',
        countrySearchPlaceholder: '搜索国家 slug 或名称，例如 england / any / England',
        countryKeyLabel: '国家 slug/key（5SIM）',
        countryKeyHint: '5SIM 使用国家文本 key，例如 <code>england</code>、<code>usa</code>、<code>indonesia</code>、<code>any</code>。',
        maxPriceLabel: '最大单价（仅 operator=any 时生效）',
        maxPriceHint: '5SIM 文档说明 maxPrice 仅在 operator 为 <code>any</code> 时生效；填 <code>-1</code> 表示不限制。',
        operatorHint: '5SIM 路径里包含 operator；当前实现默认使用 <code>any</code>，暂不在页面强制输入。',
        proxyLabel: '5SIM 专用代理（可选）',
        inspectorHint: '5SIM 支持国家、服务列表、推荐国家、运营商、运营商报价；不支持 providerIds 与静态钱包。运营商页可作为固定字段参考，但并非所有 operator 都支持当前 product。',
        providerQuotesLabel: '5SIM 不提供 provider 级报价接口。',
        walletSupported: false,
        walletHint: '5SIM 文档未提供静态钱包接口。',
    },
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadSettings();
    loadEmailServices();
    loadDatabaseInfo();
    loadProxies();
    loadCpaServices();
    loadSub2ApiServices();
    loadTmServices();
    loadSmsCountries();
    initEventListeners();
});

document.addEventListener('click', () => {
    document.querySelectorAll('.dropdown-menu.active').forEach(m => m.classList.remove('active'));
});

// 初始化标签页
function initTabs() {
    elements.tabs.forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;

            elements.tabs.forEach(b => b.classList.remove('active'));
            elements.tabContents.forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            document.getElementById(`${tab}-tab`).classList.add('active');
        });
    });
}

// 事件监听
function initEventListeners() {
    // 注册配置表单
    if (elements.registrationForm) {
        elements.registrationForm.addEventListener('submit', handleSaveRegistration);
    }

    // 备份数据库
    if (elements.backupBtn) {
        elements.backupBtn.addEventListener('click', handleBackup);
    }

    // 清理数据
    if (elements.cleanupBtn) {
        elements.cleanupBtn.addEventListener('click', handleCleanup);
    }

    // 添加邮箱服务
    if (elements.addEmailServiceBtn) {
        elements.addEmailServiceBtn.addEventListener('click', () => {
            elements.addServiceModal.classList.add('active');
            loadServiceConfigFields(elements.serviceType.value);
        });
    }

    if (elements.closeServiceModal) {
        elements.closeServiceModal.addEventListener('click', () => {
            elements.addServiceModal.classList.remove('active');
        });
    }

    if (elements.cancelAddService) {
        elements.cancelAddService.addEventListener('click', () => {
            elements.addServiceModal.classList.remove('active');
        });
    }

    if (elements.addServiceModal) {
        elements.addServiceModal.addEventListener('click', (e) => {
            if (e.target === elements.addServiceModal) {
                elements.addServiceModal.classList.remove('active');
            }
        });
    }

    // 服务类型切换
    if (elements.serviceType) {
        elements.serviceType.addEventListener('change', (e) => {
            loadServiceConfigFields(e.target.value);
        });
    }

    // 添加服务表单
    if (elements.addServiceForm) {
        elements.addServiceForm.addEventListener('submit', handleAddService);
    }

    // Outlook 批量导入展开/折叠
    if (elements.toggleImportBtn) {
        elements.toggleImportBtn.addEventListener('click', () => {
            const isHidden = elements.outlookImportBody.style.display === 'none';
            elements.outlookImportBody.style.display = isHidden ? 'block' : 'none';
            elements.toggleImportBtn.textContent = isHidden ? '收起' : '展开';
        });
    }

    // Outlook 批量导入
    if (elements.outlookImportBtn) {
        elements.outlookImportBtn.addEventListener('click', handleOutlookBatchImport);
    }

    // 清空导入数据
    if (elements.clearImportBtn) {
        elements.clearImportBtn.addEventListener('click', () => {
            elements.outlookImportData.value = '';
            elements.importResult.style.display = 'none';
        });
    }

    // 全选/取消全选
    if (elements.selectAllServices) {
        elements.selectAllServices.addEventListener('change', (e) => {
            const checkboxes = document.querySelectorAll('.service-checkbox');
            checkboxes.forEach(cb => cb.checked = e.target.checked);
            updateSelectedServices();
        });
    }

    // 代理列表相关
    if (elements.addProxyBtn) {
        elements.addProxyBtn.addEventListener('click', () => openProxyModal());
    }

    if (elements.testAllProxiesBtn) {
        elements.testAllProxiesBtn.addEventListener('click', handleTestAllProxies);
    }

    if (elements.closeProxyModal) {
        elements.closeProxyModal.addEventListener('click', closeProxyModal);
    }

    if (elements.cancelProxyBtn) {
        elements.cancelProxyBtn.addEventListener('click', closeProxyModal);
    }

    if (elements.addProxyModal) {
        elements.addProxyModal.addEventListener('click', (e) => {
            if (e.target === elements.addProxyModal) {
                closeProxyModal();
            }
        });
    }

    if (elements.proxyItemForm) {
        elements.proxyItemForm.addEventListener('submit', handleSaveProxyItem);
    }

    // 动态代理设置
    if (elements.dynamicProxyForm) {
        elements.dynamicProxyForm.addEventListener('submit', handleSaveDynamicProxy);
    }
    if (elements.proxyPreferenceForm) {
        elements.proxyPreferenceForm.addEventListener('submit', handleSaveProxyPreference);
    }
    if (elements.testProxyPreferenceBtn) {
        elements.testProxyPreferenceBtn.addEventListener('click', handleTestProxyPreference);
    }
    document.getElementById('dynamic-proxy-mode')?.addEventListener('change', updateDynamicProxyModeUi);
    document.getElementById('dynamic-proxy-provider')?.addEventListener('change', updateDynamicProxyModeUi);
    document.getElementById('dynamic-proxy-seekproxy-auth-type')?.addEventListener('change', updateDynamicProxyModeUi);
    document.getElementById('dynamic-proxy-seekproxy-country-search')?.addEventListener('input', handleSeekproxyCountrySearch);
    document.getElementById('dynamic-proxy-seekproxy-country-options')?.addEventListener('change', handleSeekproxyCountrySelected);
    document.getElementById('dynamic-proxy-seekproxy-country-options')?.addEventListener('click', handleSeekproxyCountrySelected);
    document.getElementById('dynamic-proxy-seekproxy-country-options')?.addEventListener('dblclick', handleSeekproxyCountrySelected);
    document.getElementById('dynamic-proxy-seekproxy-country')?.addEventListener('change', handleSeekproxyCountryCodeChanged);
    document.getElementById('dynamic-proxy-seekproxy-state-search')?.addEventListener('input', handleSeekproxyStateSearch);
    document.getElementById('dynamic-proxy-seekproxy-state-options')?.addEventListener('change', handleSeekproxyStateSelected);
    document.getElementById('dynamic-proxy-seekproxy-state-options')?.addEventListener('click', handleSeekproxyStateSelected);
    document.getElementById('dynamic-proxy-seekproxy-state-options')?.addEventListener('dblclick', handleSeekproxyStateSelected);
    document.getElementById('dynamic-proxy-seekproxy-city-search')?.addEventListener('input', handleSeekproxyCitySearch);
    document.getElementById('dynamic-proxy-seekproxy-city-options')?.addEventListener('change', handleSeekproxyCitySelected);
    document.getElementById('dynamic-proxy-seekproxy-city-options')?.addEventListener('click', handleSeekproxyCitySelected);
    document.getElementById('dynamic-proxy-seekproxy-city-options')?.addEventListener('dblclick', handleSeekproxyCitySelected);
    document.getElementById('proxy-preference-mode')?.addEventListener('change', updateProxyPreferenceUi);
    if (elements.testDynamicProxyBtn) {
        elements.testDynamicProxyBtn.addEventListener('click', handleTestDynamicProxy);
    }
    if (elements.proxyOperationSettingsForm) {
        elements.proxyOperationSettingsForm.addEventListener('submit', handleSaveProxyOperationSettings);
    }

    // 验证码设置
    if (elements.emailCodeForm) {
        elements.emailCodeForm.addEventListener('submit', handleSaveEmailCode);
    }

    // 短信接码设置
    if (elements.smsSettingsForm) {
        elements.smsSettingsForm.addEventListener('submit', handleSaveSmsSettings);
    }
    document.getElementById('sms-provider')?.addEventListener('change', handleSmsProviderChanged);
    document.getElementById('sms-top-countries-btn')?.addEventListener('click', loadSmsTopCountries);
    document.getElementById('sms-services-btn')?.addEventListener('click', loadSmsServices);
    document.getElementById('sms-operators-btn')?.addEventListener('click', loadSmsOperators);
    document.getElementById('sms-operator-quotes-btn')?.addEventListener('click', loadSmsOperatorQuotes);
    document.getElementById('sms-provider-quotes-btn')?.addEventListener('click', loadSmsProviderQuotes);
    document.getElementById('sms-wallet-btn')?.addEventListener('click', loadSmsStaticWallet);
    if (elements.testSmsBtn) {
        elements.testSmsBtn.addEventListener('click', handleTestSmsProvider);
    }
    const herosmsCountrySearch = document.getElementById('sms-country-search');
    if (herosmsCountrySearch) {
        herosmsCountrySearch.addEventListener('input', renderHeroSMSCountryMenu);
        herosmsCountrySearch.addEventListener('focus', renderHeroSMSCountryMenu);
        herosmsCountrySearch.addEventListener('change', handleHeroSMSCountrySearchChange);
    }
    document.addEventListener('click', (e) => {
        const box = document.getElementById('sms-country-select');
        const menu = document.getElementById('sms-country-menu');
        if (box && menu && !box.contains(e.target)) {
            menu.classList.remove('active');
        }
    });

    // Outlook 设置
    if (elements.outlookSettingsForm) {
        elements.outlookSettingsForm.addEventListener('submit', handleSaveOutlookSettings);
    }

    if (elements.webuiSettingsForm) {
        elements.webuiSettingsForm.addEventListener('submit', handleSaveWebuiSettings);
    }
    // Team Manager 服务管理
    if (elements.addTmServiceBtn) {
        elements.addTmServiceBtn.addEventListener('click', () => openTmServiceModal());
    }
    if (elements.closeTmServiceModal) {
        elements.closeTmServiceModal.addEventListener('click', closeTmServiceModal);
    }
    if (elements.cancelTmServiceBtn) {
        elements.cancelTmServiceBtn.addEventListener('click', closeTmServiceModal);
    }
    if (elements.tmServiceEditModal) {
        elements.tmServiceEditModal.addEventListener('click', (e) => {
            if (e.target === elements.tmServiceEditModal) closeTmServiceModal();
        });
    }
    if (elements.tmServiceForm) {
        elements.tmServiceForm.addEventListener('submit', handleSaveTmService);
    }
    if (elements.testTmServiceBtn) {
        elements.testTmServiceBtn.addEventListener('click', handleTestTmService);
    }

    // CPA 服务管理
    if (elements.addCpaServiceBtn) {
        elements.addCpaServiceBtn.addEventListener('click', () => openCpaServiceModal());
    }
    if (elements.closeCpaServiceModal) {
        elements.closeCpaServiceModal.addEventListener('click', closeCpaServiceModal);
    }
    if (elements.cancelCpaServiceBtn) {
        elements.cancelCpaServiceBtn.addEventListener('click', closeCpaServiceModal);
    }
    if (elements.cpaServiceEditModal) {
        elements.cpaServiceEditModal.addEventListener('click', (e) => {
            if (e.target === elements.cpaServiceEditModal) closeCpaServiceModal();
        });
    }
    if (elements.cpaServiceForm) {
        elements.cpaServiceForm.addEventListener('submit', handleSaveCpaService);
    }
    if (elements.testCpaServiceBtn) {
        elements.testCpaServiceBtn.addEventListener('click', handleTestCpaService);
    }

    // Sub2API 服务管理
    if (elements.addSub2ApiServiceBtn) {
        elements.addSub2ApiServiceBtn.addEventListener('click', () => openSub2ApiServiceModal());
    }
    if (elements.closeSub2ApiServiceModal) {
        elements.closeSub2ApiServiceModal.addEventListener('click', closeSub2ApiServiceModal);
    }
    if (elements.cancelSub2ApiServiceBtn) {
        elements.cancelSub2ApiServiceBtn.addEventListener('click', closeSub2ApiServiceModal);
    }
    if (elements.sub2ApiServiceEditModal) {
        elements.sub2ApiServiceEditModal.addEventListener('click', (e) => {
            if (e.target === elements.sub2ApiServiceEditModal) closeSub2ApiServiceModal();
        });
    }
    if (elements.sub2ApiServiceForm) {
        elements.sub2ApiServiceForm.addEventListener('submit', handleSaveSub2ApiService);
    }
    if (elements.testSub2ApiServiceBtn) {
        elements.testSub2ApiServiceBtn.addEventListener('click', handleTestSub2ApiService);
    }
}

// 加载设置
async function loadSettings() {
    try {
        const data = await api.get('/settings');

        // 动态代理设置
        dynamicProxyProfiles = data.proxy?.dynamic_profiles || data.proxy?.profiles || {};
        document.getElementById('dynamic-proxy-enabled').checked = data.proxy?.dynamic_enabled || false;
        document.getElementById('dynamic-proxy-mode').value = data.proxy?.dynamic_mode || 'api';
        document.getElementById('dynamic-proxy-provider').value = data.proxy?.dynamic_provider || 'generic';
        const currentDynamicProfileKey = getDynamicProfileKey();
        if ((!dynamicProxyProfiles[currentDynamicProfileKey] || Object.keys(dynamicProxyProfiles[currentDynamicProfileKey]).length === 0) && data.proxy?.dynamic_provider === 'seekproxy') {
            dynamicProxyProfiles[currentDynamicProfileKey] = {
                trade_no: data.proxy?.dynamic_seekproxy_trade_no || data.proxy?.seekproxy_trade_no || '',
                auth_type: data.proxy?.dynamic_seekproxy_auth_type ?? data.proxy?.seekproxy_auth_type ?? 2,
                ip_count: data.proxy?.dynamic_seekproxy_ip_count ?? data.proxy?.seekproxy_ip_count ?? 1,
                state: data.proxy?.dynamic_seekproxy_state ?? data.proxy?.seekproxy_state ?? '',
                city: data.proxy?.dynamic_seekproxy_city ?? data.proxy?.seekproxy_city ?? '',
                break_type: data.proxy?.dynamic_seekproxy_break_type ?? data.proxy?.seekproxy_break_type ?? 1,
                time: data.proxy?.dynamic_seekproxy_time ?? data.proxy?.seekproxy_time ?? 5,
                protocol: data.proxy?.dynamic_seekproxy_protocol ?? data.proxy?.seekproxy_protocol ?? 0,
                pattern: data.proxy?.dynamic_seekproxy_pattern ?? data.proxy?.seekproxy_pattern ?? 0,
                valid_code: data.proxy?.dynamic_seekproxy_valid_code ?? data.proxy?.seekproxy_valid_code ?? 0,
                country: data.proxy?.dynamic_country || data.proxy?.country || 'US'
            };
        }
        applyDynamicProfile(dynamicProxyProfiles[currentDynamicProfileKey] || {});
        const refreshProxyToggle = document.getElementById('proxy-refresh-use-proxy');
        const validateProxyToggle = document.getElementById('proxy-validate-use-proxy');
        if (refreshProxyToggle) refreshProxyToggle.checked = !!data.proxy?.refresh_use_proxy;
        if (validateProxyToggle) validateProxyToggle.checked = !!data.proxy?.validate_use_proxy;
        const dynamicKeyInput = document.getElementById('dynamic-proxy-api-key');
        const dynamicKeyStatus = document.getElementById('dynamic-proxy-api-key-status');
        if (dynamicKeyInput) {
            dynamicKeyInput.value = '';
            dynamicKeyInput.dataset.hasKey = data.proxy?.has_dynamic_api_key ? '1' : '0';
            dynamicKeyInput.placeholder = data.proxy?.has_dynamic_api_key ? '已配置，留空保持不变' : '留空保持不变';
        }
        if (dynamicKeyStatus) {
            dynamicKeyStatus.textContent = data.proxy?.has_dynamic_api_key ? '已保存 API Key' : '未保存 API Key';
        }
        const providerAppkeyInput = document.getElementById('dynamic-proxy-provider-appkey');
        const providerAppkeyStatus = document.getElementById('dynamic-proxy-provider-appkey-status');
        if (providerAppkeyInput) {
            providerAppkeyInput.value = '';
            providerAppkeyInput.dataset.hasKey = data.proxy?.has_dynamic_provider_appkey ? '1' : '0';
            providerAppkeyInput.placeholder = data.proxy?.has_dynamic_provider_appkey ? '已配置，留空保持不变' : '请输入 AppKey';
        }
        if (providerAppkeyStatus) {
            providerAppkeyStatus.textContent = data.proxy?.has_dynamic_provider_appkey ? '已保存 AppKey' : '未保存 AppKey';
        }
        const seekproxyKeyInput = document.getElementById('dynamic-proxy-seekproxy-key');
        const seekproxyKeyStatus = document.getElementById('dynamic-proxy-seekproxy-key-status');
        if (seekproxyKeyInput) {
            seekproxyKeyInput.value = '';
            seekproxyKeyInput.dataset.hasKey = data.proxy?.has_dynamic_seekproxy_key ? '1' : '0';
            seekproxyKeyInput.placeholder = data.proxy?.has_dynamic_seekproxy_key ? '已配置，留空保持不变' : '请输入 SeekProxy key';
        }
        if (seekproxyKeyStatus) {
            seekproxyKeyStatus.textContent = data.proxy?.has_dynamic_seekproxy_key ? '已保存 key' : '未保存 key';
        }
        const dynamicPasswordInput = document.getElementById('dynamic-proxy-password');
        const dynamicPasswordStatus = document.getElementById('dynamic-proxy-password-status');
        if (dynamicPasswordInput) {
            dynamicPasswordInput.value = '';
            dynamicPasswordInput.dataset.hasPassword = data.proxy?.has_dynamic_password ? '1' : '0';
            dynamicPasswordInput.placeholder = data.proxy?.has_dynamic_password ? '已配置，留空保持不变' : '请输入代理密码';
        }
        if (dynamicPasswordStatus) {
            dynamicPasswordStatus.textContent = data.proxy?.has_dynamic_password ? '已保存代理密码' : '未保存代理密码';
        }
        const proxyDiagnostics = data.proxy?.diagnostics || {};
        const preferenceMode = document.getElementById('proxy-preference-mode');
        const preferredFixedId = document.getElementById('proxy-preferred-fixed-id');
        if (preferenceMode) preferenceMode.value = data.proxy?.preference_mode || 'auto';
        if (preferredFixedId) {
            preferredFixedId.dataset.preferredValue = String(data.proxy?.preferred_fixed_id || 0);
            preferredFixedId.value = String(data.proxy?.preferred_fixed_id || 0);
        }
        const connectRetryCount = document.getElementById('proxy-connect-retry-count');
        if (connectRetryCount) connectRetryCount.value = data.proxy?.connect_retry_count || 3;
        const diagSource = document.getElementById('proxy-diagnostics-source');
        const diagDynamicKey = document.getElementById('proxy-diagnostics-dynamic-key');
        const diagStaticPassword = document.getElementById('proxy-diagnostics-static-password');
        const diagDbPath = document.getElementById('proxy-diagnostics-db-path');
        if (diagSource) diagSource.textContent = proxyDiagnostics.settings_source || '-';
        if (diagDynamicKey) diagDynamicKey.textContent = proxyDiagnostics.has_dynamic_api_key ? '是' : '否';
        if (diagStaticPassword) diagStaticPassword.textContent = proxyDiagnostics.has_static_proxy_password ? '是' : '否';
        if (diagDbPath) diagDbPath.textContent = proxyDiagnostics.database_path || proxyDiagnostics.database_url || '-';
        updateDynamicProxyModeUi();
        updateProxyPreferenceUi();

        // 注册配置
        document.getElementById('max-retries').value = data.registration?.max_retries || 3;
        document.getElementById('timeout').value = data.registration?.timeout || 120;
        document.getElementById('password-length').value = data.registration?.default_password_length || 12;
        const flowSelect = document.getElementById('registration-flow-template');
        if (flowSelect) {
            const templates = data.registration?.templates || [];
            if (templates.length > 0) {
                flowSelect.innerHTML = templates.map(tpl => `
                    <option value="${tpl.id}">${tpl.name || tpl.id}</option>
                `).join('');
            }
            flowSelect.value = data.registration?.flow_template || 'default';
        }
        document.getElementById('sleep-min').value = data.registration?.sleep_min || 5;
        document.getElementById('sleep-max').value = data.registration?.sleep_max || 30;

        // 验证码等待配置
        if (data.email_code) {
            document.getElementById('email-code-timeout').value = data.email_code.timeout || 120;
            document.getElementById('email-code-poll-interval').value = data.email_code.poll_interval || 3;
        }

        // 短信接码配置
        if (data.herosms) {
            document.getElementById('sms-provider').value = data.herosms.provider || 'herosms';
            document.getElementById('sms-operator').value = data.herosms.operator || '';
            document.getElementById('sms-provider-ids').value = data.herosms.provider_ids || '';
            document.getElementById('sms-except-provider-ids').value = data.herosms.except_provider_ids || '';
            document.getElementById('sms-phone-exception').value = data.herosms.phone_exception || '';
            document.getElementById('sms-country-key').value = data.herosms.country_key || '';
            document.getElementById('sms-min-price').value = data.herosms.min_price ?? '-1';
            document.getElementById('sms-reuse-platform').checked = !!data.herosms.reuse_platform;
            document.getElementById('sms-voice').checked = !!data.herosms.voice;
            document.getElementById('sms-forwarding').checked = !!data.herosms.forwarding;
            document.getElementById('sms-forwarding-number').value = data.herosms.forwarding_number || '';
            document.getElementById('sms-provider-failover-enabled').checked = data.herosms.provider_failover_enabled !== false;
            document.getElementById('sms-provider-fail-threshold').value = data.herosms.provider_fail_threshold || 3;
            document.getElementById('sms-enabled').checked = !!data.herosms.enabled;
            document.getElementById('sms-service').value = data.herosms.service || 'dr';
            document.getElementById('sms-country').value = data.herosms.country || 187;
            updateHeroSMSCountrySearch(data.herosms.country || 187);
            document.getElementById('sms-max-price').value = data.herosms.max_price ?? '-1';
            document.getElementById('sms-proxy').value = data.herosms.proxy || '';
            document.getElementById('sms-timeout').value = data.herosms.timeout || 30;
            document.getElementById('sms-verify-timeout').value = data.herosms.verify_timeout || 180;
            document.getElementById('sms-poll-interval').value = data.herosms.poll_interval || 3;
            document.getElementById('sms-lowest-price-first').checked = data.herosms.lowest_price_first !== false;
            document.getElementById('sms-max-number-attempts').value = data.herosms.max_number_attempts || 1;
            document.getElementById('sms-target-number-index').value = data.herosms.target_number_index || 1;
            document.getElementById('sms-price-relax-enabled').checked = data.herosms.price_relax_enabled !== false;
            document.getElementById('sms-price-relax-max-multiplier').value = data.herosms.price_relax_max_multiplier || 5;
            document.getElementById('sms-retry-per-provider').value = data.herosms.retry_per_provider || 1;
            document.getElementById('sms-reuse-enabled').checked = !!data.herosms.reuse_enabled;
            document.getElementById('sms-reuse-max-uses').value = data.herosms.reuse_max_uses || 2;
            const keyInput = document.getElementById('sms-api-key');
            const keyStatus = document.getElementById('sms-api-key-status');
            const providerName = data.herosms.provider_display_name || data.herosms.provider || 'HeroSMS';
            if (keyInput) {
                keyInput.value = '';
                keyInput.dataset.hasKey = data.herosms.has_api_key ? '1' : '0';
                keyInput.placeholder = data.herosms.has_api_key ? `留空保持已保存 ${providerName} Key 不变` : `请输入 ${providerName} API Key`;
            }
            if (keyStatus) {
                keyStatus.dataset.hasKey = data.herosms.has_api_key ? '1' : '0';
                keyStatus.textContent = data.herosms.has_api_key ? `当前已保存 ${providerName} API Key:是` : `当前已保存 ${providerName} API Key:否`;
            }
            updateSmsProviderUi(data.herosms.provider || 'herosms');
            handleSmsProviderChanged();
        }

        // 加载 Outlook 设置
        loadOutlookSettings();

        // Web UI 访问密码提示
        if (data.webui?.has_access_password) {
            const input = document.getElementById('webui-access-password');
            if (input) {
                input.value = '';
                input.placeholder = '已配置，留空保持不变';
            }
        }

    } catch (error) {
        console.error('加载设置失败:', error);
        toast.error('加载设置失败');
    }
}

// 保存 Web UI 设置
async function handleSaveWebuiSettings(e) {
    e.preventDefault();

    const accessPassword = document.getElementById('webui-access-password').value;
    const payload = {
        access_password: accessPassword || null
    };

    try {
        await api.post('/settings/webui', payload);
        toast.success('Web UI 设置已更新');
        document.getElementById('webui-access-password').value = '';
    } catch (error) {
        console.error('保存 Web UI 设置失败:', error);
        toast.error('保存 Web UI 设置失败');
    }
}

// 加载邮箱服务
async function loadEmailServices() {
    // 检查元素是否存在
    if (!elements.emailServicesTable) return;

    try {
        const data = await api.get('/email-services');
        renderEmailServices(data.services);
    } catch (error) {
        console.error('加载邮箱服务失败:', error);
        if (elements.emailServicesTable) {
            elements.emailServicesTable.innerHTML = `
                <tr>
                    <td colspan="7">
                        <div class="empty-state">
                            <div class="empty-state-icon">❌</div>
                            <div class="empty-state-title">加载失败</div>
                        </div>
                    </td>
                </tr>
            `;
        }
    }
}

// 渲染邮箱服务
function renderEmailServices(services) {
    // 检查元素是否存在
    if (!elements.emailServicesTable) return;

    if (services.length === 0) {
        elements.emailServicesTable.innerHTML = `
            <tr>
                <td colspan="7">
                    <div class="empty-state">
                        <div class="empty-state-icon">📭</div>
                        <div class="empty-state-title">暂无配置</div>
                        <div class="empty-state-description">点击上方"添加服务"按钮添加邮箱服务</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    elements.emailServicesTable.innerHTML = services.map(service => `
        <tr data-service-id="${service.id}">
            <td>
                <input type="checkbox" class="service-checkbox" data-id="${service.id}"
                    onchange="updateSelectedServices()">
            </td>
            <td>${escapeHtml(service.name)}</td>
            <td>${getServiceTypeText(service.service_type)}</td>
            <td title="${service.enabled ? '已启用' : '已禁用'}">${service.enabled ? '✅' : '⭕'}</td>
            <td>${service.priority}</td>
            <td>${format.date(service.last_used)}</td>
            <td>
                <div class="action-buttons">
                    <button class="btn btn-ghost btn-sm" onclick="testService(${service.id})" title="测试">
                        🔌
                    </button>
                    <button class="btn btn-ghost btn-sm" onclick="toggleService(${service.id}, ${!service.enabled})" title="${service.enabled ? '禁用' : '启用'}">
                        ${service.enabled ? '🔒' : '🔓'}
                    </button>
                    <button class="btn btn-ghost btn-sm" onclick="deleteService(${service.id})" title="删除">
                        🗑️
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

// 加载数据库信息
async function loadDatabaseInfo() {
    try {
        const data = await api.get('/settings/database');

        document.getElementById('db-size').textContent = `${data.database_size_mb} MB`;
        document.getElementById('db-accounts').textContent = format.number(data.accounts_count);
        document.getElementById('db-services').textContent = format.number(data.email_services_count);
        document.getElementById('db-tasks').textContent = format.number(data.tasks_count);

    } catch (error) {
        console.error('加载数据库信息失败:', error);
    }
}

// 保存注册配置
async function handleSaveRegistration(e) {
    e.preventDefault();

    const data = {
        max_retries: parseInt(document.getElementById('max-retries').value),
        timeout: parseInt(document.getElementById('timeout').value),
        default_password_length: parseInt(document.getElementById('password-length').value),
        flow_template: document.getElementById('registration-flow-template')?.value || 'default',
        sleep_min: parseInt(document.getElementById('sleep-min').value),
        sleep_max: parseInt(document.getElementById('sleep-max').value),
    };

    try {
        await api.post('/settings/registration', data);
        toast.success('注册配置已保存');
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

// 保存验证码等待配置
async function handleSaveEmailCode(e) {
    e.preventDefault();

    const timeout = parseInt(document.getElementById('email-code-timeout').value);
    const pollInterval = parseInt(document.getElementById('email-code-poll-interval').value);

    // 客户端验证
    if (timeout < 30 || timeout > 600) {
        toast.error('等待超时必须在 30-600 秒之间');
        return;
    }
    if (pollInterval < 1 || pollInterval > 30) {
        toast.error('轮询间隔必须在 1-30 秒之间');
        return;
    }

    const data = {
        timeout: timeout,
        poll_interval: pollInterval
    };

    try {
        await api.post('/settings/email-code', data);
        toast.success('验证码配置已保存');
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

// 保存短信接码配置
async function handleSaveSmsSettings(e) {
    e.preventDefault();

    const data = {
        provider: document.getElementById('sms-provider').value || 'herosms',
        operator: document.getElementById('sms-operator').value.trim(),
        provider_ids: document.getElementById('sms-provider-ids').value.trim(),
        except_provider_ids: document.getElementById('sms-except-provider-ids').value.trim(),
        phone_exception: document.getElementById('sms-phone-exception').value.trim(),
        country_key: document.getElementById('sms-country-key').value.trim(),
        min_price: document.getElementById('sms-min-price').value.trim() || '-1',
        reuse_platform: document.getElementById('sms-reuse-platform').checked,
        voice: document.getElementById('sms-voice').checked,
        forwarding: document.getElementById('sms-forwarding').checked,
        forwarding_number: document.getElementById('sms-forwarding-number').value.trim(),
        provider_failover_enabled: document.getElementById('sms-provider-failover-enabled').checked,
        provider_fail_threshold: parseInt(document.getElementById('sms-provider-fail-threshold').value) || 3,
        enabled: document.getElementById('sms-enabled').checked,
        api_key: document.getElementById('sms-api-key').value || null,
        service: document.getElementById('sms-service').value.trim() || 'dr',
        country: parseInt(document.getElementById('sms-country').value) || 187,
        max_price: document.getElementById('sms-max-price').value.trim() || '-1',
        proxy: document.getElementById('sms-proxy').value.trim(),
        timeout: parseInt(document.getElementById('sms-timeout').value) || 30,
        verify_timeout: parseInt(document.getElementById('sms-verify-timeout').value) || 180,
        poll_interval: parseInt(document.getElementById('sms-poll-interval').value) || 3,
        lowest_price_first: document.getElementById('sms-lowest-price-first').checked,
        max_number_attempts: parseInt(document.getElementById('sms-max-number-attempts').value) || 1,
        target_number_index: parseInt(document.getElementById('sms-target-number-index').value) || 1,
        price_relax_enabled: document.getElementById('sms-price-relax-enabled').checked,
        price_relax_max_multiplier: parseInt(document.getElementById('sms-price-relax-max-multiplier').value) || 5,
        retry_per_provider: parseInt(document.getElementById('sms-retry-per-provider').value) || 1,
        reuse_enabled: document.getElementById('sms-reuse-enabled').checked,
        reuse_max_uses: parseInt(document.getElementById('sms-reuse-max-uses').value) || 2,
    };

    if (data.provider !== '5sim' && data.country <= 0) {
        toast.error('国家代码必须大于 0');
        return;
    }
    if (data.provider === '5sim' && !data.country_key) {
        toast.error('5SIM 需要填写国家 slug/key');
        return;
    }
    if (data.verify_timeout < 30 || data.verify_timeout > 600) {
        toast.error('短信等待超时必须在 30-600 秒之间');
        return;
    }
    if (data.max_number_attempts < 1 || data.max_number_attempts > 20) {
        toast.error('最大换号次数必须在 1-20 之间');
        return;
    }
    if (data.target_number_index < 1 || data.target_number_index > data.max_number_attempts) {
        toast.error('使用第 N 个号码必须在 1 到最大换号次数之间');
        return;
    }
    if (data.price_relax_max_multiplier < 1 || data.price_relax_max_multiplier > 20) {
        toast.error('价格放宽最大倍数必须在 1-20 之间');
        return;
    }
    if (data.retry_per_provider < 1 || data.retry_per_provider > 50) {
        toast.error('同组合取号重试次数必须在 1-50 之间');
        return;
    }
    if (data.provider_fail_threshold < 1 || data.provider_fail_threshold > 10) {
        toast.error('同 provider 连续失败阈值必须在 1-10 之间');
        return;
    }
    if (data.reuse_max_uses < 1 || data.reuse_max_uses > 5) {
        toast.error('号码复用次数必须在 1-5 之间');
        return;
    }

    try {
        await api.post('/settings/sms', data);
        toast.success(data.enabled ? '短信平台已启用' : '短信平台已关闭');
        document.getElementById('sms-api-key').value = '';
        loadSettings();
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

async function handleTestSmsProvider() {
    const btn = elements.testSmsBtn;
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '测试中...';
    try {
        const provider = document.getElementById('sms-provider').value || 'herosms';
        const result = await api.post('/settings/sms/test', {
            api_key: document.getElementById('sms-api-key').value || null,
            proxy: document.getElementById('sms-proxy').value.trim() || '',
            provider,
        });
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (error) {
        toast.error('测试失败: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '🔌 测试短信平台连接';
    }
}

async function loadSmsCountries() {
    const menu = document.getElementById('sms-country-menu');
    if (!menu) return;
    try {
        const provider = document.getElementById('sms-provider')?.value || 'herosms';
        const data = await api.get(`/settings/sms/countries?provider=${encodeURIComponent(provider)}`);
        herosmsCountries = data.countries || [];
        const current = parseInt(document.getElementById('sms-country')?.value) || 187;
        const currentKey = document.getElementById('sms-country-key')?.value?.trim() || '';
        updateHeroSMSCountrySearch(provider === '5sim' ? currentKey : current);
    } catch (error) {
        console.warn('加载短信平台国家列表失败:', error);
        menu.innerHTML = '<div class="search-select-item">国家列表加载失败</div>';
    }
}

function handleHeroSMSCountrySearchChange() {
    const input = document.getElementById('sms-country-search');
    const countryInput = document.getElementById('sms-country');
    if (!input || !countryInput) return;
    const value = input.value.trim();
    const matched = herosmsCountries.find(c => c.display === value)
        || herosmsCountries.find(c => String(c.code) === value)
        || herosmsCountries.find(c => (c.country_key || '').toLowerCase() === value.toLowerCase())
        || herosmsCountries.find(c => (c.name || '').toLowerCase() === value.toLowerCase());
    if (matched) {
        selectHeroSMSCountry(matched);
    }
}

function updateHeroSMSCountrySearch(code) {
    const input = document.getElementById('sms-country-search');
    if (!input) return;
    const matched = herosmsCountries.find(c => String(c.code) === String(code))
        || herosmsCountries.find(c => String(c.country_key || '') === String(code));
    input.value = matched ? matched.display : '';
}

function getSmsProviderUiConfig(provider) {
    return smsProviderUiConfig[provider] || smsProviderUiConfig.herosms;
}

function updateSmsProviderUi(provider) {
    const cfg = getSmsProviderUiConfig(provider);
    const keyStatus = document.getElementById('sms-api-key-status');
    const keyInput = document.getElementById('sms-api-key');
    const hasSavedKey = keyStatus?.dataset.hasKey === '1';
    const serviceInput = document.getElementById('sms-service');

    const textMap = {
        'sms-provider-enabled-hint': cfg.enabledHint,
        'sms-api-key-label': `${cfg.label} API Key`,
        'sms-api-key-hint': cfg.apiKeyHint,
        'sms-provider-docs-title': cfg.docsTitle,
        'sms-provider-docs-desc': cfg.docsDesc,
        'sms-provider-docs-fields': cfg.docsFields,
        'sms-country-label': cfg.countryLabel,
        'sms-country-hint': cfg.countryHint,
        'sms-country-key-label': cfg.countryKeyLabel,
        'sms-country-key-hint': cfg.countryKeyHint,
        'sms-max-price-label': cfg.maxPriceLabel,
        'sms-max-price-hint': cfg.maxPriceHint,
        'sms-operator-hint': cfg.operatorHint,
        'sms-proxy-label': cfg.proxyLabel,
        'sms-inspector-hint': cfg.inspectorHint,
    };
    Object.entries(textMap).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = value;
    });

    const docsLinks = document.getElementById('sms-provider-docs-links');
    if (docsLinks) docsLinks.innerHTML = cfg.docsLinks;

    const searchInput = document.getElementById('sms-country-search');
    if (searchInput) searchInput.placeholder = cfg.countrySearchPlaceholder;

    if (serviceInput && (!serviceInput.value || ['dr', 'openai'].includes(serviceInput.value.trim()))) {
        serviceInput.placeholder = `例如 ${cfg.serviceExample}`;
    }
    if (serviceInput) {
        const providerDefaults = {
            herosms: 'dr',
            smsbower: 'dr',
            '5sim': 'openai',
        };
        const current = (serviceInput.value || '').trim().toLowerCase();
        const knownDefaults = new Set(Object.values(providerDefaults));
        if (!current || knownDefaults.has(current)) {
            serviceInput.value = providerDefaults[provider] || cfg.serviceExample;
        }
    }
    const serviceHint = document.getElementById('sms-service-hint');
    if (serviceHint) serviceHint.innerHTML = cfg.serviceHint;

    if (keyInput) {
        keyInput.placeholder = hasSavedKey ? `留空保持已保存 ${cfg.label} Key 不变` : `请输入 ${cfg.label} API Key`;
    }
    if (keyStatus) {
        keyStatus.textContent = hasSavedKey ? `当前已保存 ${cfg.label} API Key:是` : `当前已保存 ${cfg.label} API Key:否`;
    }

    const walletBtn = document.getElementById('sms-wallet-btn');
    if (walletBtn) {
        walletBtn.disabled = !cfg.walletSupported;
        walletBtn.title = cfg.walletHint;
    }
    const providerQuotesBtn = document.getElementById('sms-provider-quotes-btn');
    if (providerQuotesBtn) {
        providerQuotesBtn.disabled = provider === 'herosms' || provider === '5sim';
        providerQuotesBtn.title = cfg.providerQuotesLabel;
    }
    const operatorsBtn = document.getElementById('sms-operators-btn');
    const operatorQuotesBtn = document.getElementById('sms-operator-quotes-btn');
    const operatorGroup = document.querySelector('.provider-operator-group');
    const operatorSupported = provider === 'herosms' || provider === '5sim';
    if (operatorsBtn) operatorsBtn.disabled = !operatorSupported;
    if (operatorQuotesBtn) operatorQuotesBtn.disabled = !operatorSupported;
    if (operatorGroup) operatorGroup.style.display = operatorSupported ? '' : 'none';
}

function renderHeroSMSCountryMenu() {
    const input = document.getElementById('sms-country-search');
    const menu = document.getElementById('sms-country-menu');
    if (!input || !menu) return;
    const query = input.value.trim().toLowerCase();
    const items = herosmsCountries.filter(c => {
        const haystack = [
            c.code,
            c.name,
            c.zh_name,
            c.en_name,
            c.display
        ].join(' ').toLowerCase();
        return !query || haystack.includes(query);
    }).slice(0, 80);

    if (items.length === 0) {
        menu.innerHTML = '<div class="search-select-item">没有匹配的国家</div>';
    } else {
        menu.innerHTML = items.map(c => `
            <div class="search-select-item" data-code="${c.code}">
                ${escapeHtml(c.display)}
                <span class="muted">代码 ${c.code}</span>
            </div>
        `).join('');
        menu.querySelectorAll('.search-select-item[data-code]').forEach(item => {
            item.addEventListener('click', () => {
                const country = herosmsCountries.find(c => String(c.code) === String(item.dataset.code));
                if (country) selectHeroSMSCountry(country);
            });
        });
    }
    menu.classList.add('active');
}

function selectHeroSMSCountry(country) {
    const input = document.getElementById('sms-country-search');
    const countryInput = document.getElementById('sms-country');
    const countryKeyInput = document.getElementById('sms-country-key');
    const menu = document.getElementById('sms-country-menu');
    if (countryInput && country.code != null) countryInput.value = country.code;
    if (countryKeyInput) countryKeyInput.value = country.country_key || '';
    if (input) input.value = country.display;
    if (menu) menu.classList.remove('active');
}

const loadSmsCountriesCompat = loadSmsCountries;
const loadHeroSMSCountries = loadSmsCountries;

// 兼容旧命名，避免其他地方引用时出错
const handleSaveHeroSMS = handleSaveSmsSettings;
const handleTestHeroSMS = handleTestSmsProvider;

function setSmsInspectorHtml(html) {
    const box = document.getElementById('sms-provider-inspector');
    if (box) box.innerHTML = html;
}

function getCurrentSmsContext() {
    return {
        provider: document.getElementById('sms-provider').value || 'herosms',
        service: document.getElementById('sms-service').value.trim() || 'dr',
        country: parseInt(document.getElementById('sms-country').value) || 0,
        countryKey: document.getElementById('sms-country-key').value.trim(),
    };
}

function getSmsProviderMeta(provider) {
    const cfg = getSmsProviderUiConfig(provider);
    return {
        label: cfg.label,
        providerQuotesLabel: cfg.providerQuotesLabel,
        walletHint: cfg.walletHint,
    };
}

async function loadSmsTopCountries() {
    try {
        const { service, provider, country, countryKey } = getCurrentSmsContext();
        const meta = getSmsProviderMeta(provider);
        setSmsInspectorHtml(`正在加载 ${escapeHtml(meta.label)} 推荐国家...`);
        const data = await api.get(`/settings/sms/top-countries?service=${encodeURIComponent(service)}&provider=${encodeURIComponent(provider)}&country=${country || ''}&country_key=${encodeURIComponent(countryKey)}`);
        smsInspectorState.topCountries = data.items || [];
        if (!smsInspectorState.topCountries.length) {
            setSmsInspectorHtml(`${escapeHtml(meta.label)} 当前没有解析到推荐国家数据。`);
            return;
        }
        setSmsInspectorHtml(`
            <div style="font-weight:600;margin-bottom:8px;">${escapeHtml(meta.label)} 推荐国家（按价格/库存排序）</div>
            ${smsInspectorState.topCountries.slice(0, 10).map(item => `
                <div style="padding:4px 0;border-bottom:1px dashed var(--border-color);">
                    <strong>${escapeHtml(item.apiName || item.isoCode || String(item.heroSmsCountry))}</strong>
                    <span style="margin-left:8px;">国家码: ${item.heroSmsCountry ?? item.country_key ?? '-'}</span>
                    <span style="margin-left:8px;">价格: ${item.price ?? '-'}</span>
                    <span style="margin-left:8px;">库存: ${item.count ?? '-'}</span>
                </div>
            `).join('')}
        `);
    } catch (error) {
        setSmsInspectorHtml(`加载推荐国家失败:${escapeHtml(error.message)}`);
    }
}

async function loadSmsServices() {
    try {
        const { provider, country, countryKey } = getCurrentSmsContext();
        const meta = getSmsProviderMeta(provider);
        setSmsInspectorHtml(`正在加载 ${escapeHtml(meta.label)} 服务列表...`);
        const data = await api.get(`/settings/sms/services?provider=${encodeURIComponent(provider)}&country=${country || ''}&country_key=${encodeURIComponent(countryKey)}`);
        const items = data.items || [];
        if (!items.length) {
            setSmsInspectorHtml(`${escapeHtml(meta.label)} 没有返回服务列表。`);
            return;
        }
        setSmsInspectorHtml(`
            <div style="font-weight:600;margin-bottom:8px;">${escapeHtml(meta.label)} 服务列表</div>
            ${items.slice(0, 80).map(item => `
                <div style="padding:4px 0;border-bottom:1px dashed var(--border-color);">
                    <strong>${escapeHtml(item.code || '-')}</strong>
                    <span style="margin-left:8px;">${escapeHtml(item.name || '-')}</span>
                </div>
            `).join('')}
        `);
    } catch (error) {
        setSmsInspectorHtml(`加载服务列表失败:${escapeHtml(error.message)}`);
    }
}

async function loadSmsOperators() {
    try {
        const { country, service, provider, countryKey } = getCurrentSmsContext();
        const meta = getSmsProviderMeta(provider);
        if (!country) {
            if (provider !== '5sim' || !countryKey) {
                toast.error('请先选择国家');
                return;
            }
        }
        setSmsInspectorHtml(`正在加载 ${escapeHtml(meta.label)} 运营商...`);
        const data = await api.get(`/settings/sms/operators?country=${country || ''}&service=${encodeURIComponent(service)}&provider=${encodeURIComponent(provider)}&country_key=${encodeURIComponent(countryKey)}`);
        smsInspectorState.operators = data.items || [];
        if (!smsInspectorState.operators.length) {
            setSmsInspectorHtml(`${escapeHtml(meta.label)} 当前国家没有解析到运营商列表。`);
            return;
        }
        setSmsInspectorHtml(`
            <div style="font-weight:600;margin-bottom:8px;">${escapeHtml(meta.label)} 运营商列表</div>
            ${smsInspectorState.operators.map(op => `
                <button type="button" class="btn btn-secondary btn-sm sms-operator-chip" data-operator="${escapeHtml(op)}" style="margin:0 6px 6px 0;">${escapeHtml(op)}</button>
            `).join('')}
            <div style="margin-top:8px;color:var(--text-muted);font-size:0.82rem;">点击运营商名称可直接填入“运营商”输入框，并自动刷新运营商报价。</div>
        `);
        document.querySelectorAll('.sms-operator-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                const input = document.getElementById('sms-operator');
                if (input) {
                    input.value = btn.dataset.operator || '';
                    loadSmsOperatorQuotes();
                }
            });
        });
    } catch (error) {
        setSmsInspectorHtml(`加载运营商失败:${escapeHtml(error.message)}`);
    }
}

async function loadSmsOperatorQuotes() {
    try {
        const { country, service, provider, countryKey } = getCurrentSmsContext();
        const meta = getSmsProviderMeta(provider);
        if (!country) {
            if (provider !== '5sim' || !countryKey) {
                toast.error('请先选择国家');
                return;
            }
        }
        setSmsInspectorHtml(`正在加载 ${escapeHtml(meta.label)} 运营商报价...`);
        const data = await api.get(`/settings/sms/operator-quotes?country=${country || ''}&service=${encodeURIComponent(service)}&provider=${encodeURIComponent(provider)}&country_key=${encodeURIComponent(countryKey)}`);
        smsInspectorState.quotes = data.items || [];
        if (!smsInspectorState.quotes.length) {
            setSmsInspectorHtml(`${escapeHtml(meta.label)} 当前国家没有解析到运营商报价。`);
            return;
        }
        setSmsInspectorHtml(`
            <div style="font-weight:600;margin-bottom:8px;">${escapeHtml(meta.label)} 运营商报价</div>
            ${smsInspectorState.quotes.map(item => `
                <div style="padding:4px 0;border-bottom:1px dashed var(--border-color);">
                    <strong>${escapeHtml(item.operator || '-')}</strong>
                    <span style="margin-left:8px;">价格: ${item.price ?? '-'}</span>
                    <span style="margin-left:8px;">库存: ${item.count ?? '-'}</span>
                    ${item.error ? `<span style="margin-left:8px;color:var(--danger-color);">错误: ${escapeHtml(item.error)}</span>` : ''}
                </div>
            `).join('')}
        `);
    } catch (error) {
        setSmsInspectorHtml(`加载运营商报价失败:${escapeHtml(error.message)}`);
    }
}

async function loadSmsProviderQuotes() {
    try {
        const { country, service, provider, countryKey } = getCurrentSmsContext();
        const meta = getSmsProviderMeta(provider);
        if (!country) {
            if (provider !== '5sim' || !countryKey) {
                toast.error('请先选择国家');
                return;
            }
        }
        setSmsInspectorHtml(`正在加载 ${escapeHtml(meta.label)} Provider 级报价...`);
        const data = await api.get(`/settings/sms/provider-quotes?country=${country || ''}&service=${encodeURIComponent(service)}&provider=${encodeURIComponent(provider)}&country_key=${encodeURIComponent(countryKey)}`);
        const items = data.items || [];
        if (!items.length) {
            setSmsInspectorHtml(meta.providerQuotesLabel);
            return;
        }
        setSmsInspectorHtml(`
            <div style="font-weight:600;margin-bottom:8px;">${escapeHtml(meta.label)} Provider 级报价</div>
            ${items.map(item => `
                <div style="padding:4px 0;border-bottom:1px dashed var(--border-color);">
                    <strong>provider_id=${escapeHtml(String(item.provider_id || '-'))}</strong>
                    <span style="margin-left:8px;">价格: ${item.price ?? '-'}</span>
                    <span style="margin-left:8px;">库存: ${item.count ?? '-'}</span>
                </div>
            `).join('')}
            <div style="margin-top:8px;color:var(--text-muted);font-size:0.82rem;">${escapeHtml(meta.providerQuotesLabel)}</div>
        `);
    } catch (error) {
        setSmsInspectorHtml(`加载 Provider 级报价失败:${escapeHtml(error.message)}`);
    }
}

async function loadSmsStaticWallet() {
    try {
        const { provider } = getCurrentSmsContext();
        const meta = getSmsProviderMeta(provider);
        setSmsInspectorHtml(`正在加载 ${escapeHtml(meta.label)} 静态钱包...`);
        const data = await api.get(`/settings/sms/static-wallet?coin=usdt&network=tron&provider=${encodeURIComponent(provider)}`);
        setSmsInspectorHtml(`
            <div style="font-weight:600;margin-bottom:8px;">${escapeHtml(meta.label)} 静态钱包</div>
            <div>coin=${escapeHtml(data.coin || 'usdt')}</div>
            <div>network=${escapeHtml(data.network || 'tron')}</div>
            <div style="margin-top:6px;word-break:break-all;"><strong>${escapeHtml(data.wallet?.wallet_address || '未返回钱包地址')}</strong></div>
        `);
    } catch (error) {
        setSmsInspectorHtml(`加载静态钱包失败:${escapeHtml(error.message)}`);
    }
}

function handleSmsProviderChanged() {
    const provider = document.getElementById('sms-provider').value || 'herosms';
    const countryInput = document.getElementById('sms-country');
    const countryKeyInput = document.getElementById('sms-country-key');
    const providerIdsInput = document.getElementById('sms-provider-ids');
    const exceptProviderIdsInput = document.getElementById('sms-except-provider-ids');
    const phoneExceptionInput = document.getElementById('sms-phone-exception');
    const minPriceInput = document.getElementById('sms-min-price');
    const reuseCheckbox = document.getElementById('sms-reuse-platform');
    const voiceCheckbox = document.getElementById('sms-voice');
    const forwardingCheckbox = document.getElementById('sms-forwarding');
    const forwardingNumberInput = document.getElementById('sms-forwarding-number');
    if (countryInput) countryInput.disabled = provider === '5sim';
    if (countryKeyInput) countryKeyInput.disabled = provider !== '5sim';
    if (providerIdsInput) providerIdsInput.disabled = provider !== 'smsbower';
    if (exceptProviderIdsInput) exceptProviderIdsInput.disabled = provider !== 'smsbower';
    if (phoneExceptionInput) phoneExceptionInput.disabled = provider !== 'smsbower';
    if (minPriceInput) minPriceInput.disabled = provider === 'herosms';
    if (reuseCheckbox) reuseCheckbox.disabled = provider !== '5sim';
    if (voiceCheckbox) voiceCheckbox.disabled = provider !== '5sim';
    if (forwardingCheckbox) forwardingCheckbox.disabled = provider !== '5sim';
    if (forwardingNumberInput) forwardingNumberInput.disabled = provider !== '5sim';

    document.querySelectorAll('.provider-group').forEach(el => {
        el.style.display = '';
    });
    document.querySelectorAll('.provider-smsbower').forEach(el => {
        el.style.display = provider === 'smsbower' ? '' : 'none';
    });
    document.querySelectorAll('.provider-5sim').forEach(el => {
        el.style.display = provider === '5sim' ? '' : 'none';
    });
    document.querySelectorAll('.provider-country-key').forEach(el => {
        el.style.display = provider === '5sim' ? '' : 'none';
    });
    document.querySelectorAll('.provider-numeric-country').forEach(el => {
        el.style.display = provider === '5sim' ? 'none' : '';
    });
    updateSmsProviderUi(provider);
    setSmsInspectorHtml(`已切换到 <strong>${escapeHtml(provider)}</strong>，下方查询按钮将基于当前表单值实时查询。`);
    loadSmsCountries();
    if (provider === '5sim') {
        const service = document.getElementById('sms-service')?.value?.trim() || '';
        const countryKey = document.getElementById('sms-country-key')?.value?.trim() || '';
        if (service && countryKey) {
            loadSmsOperators();
        }
    }
}

// 备份数据库
async function handleBackup() {
    elements.backupBtn.disabled = true;
    elements.backupBtn.innerHTML = '<span class="loading-spinner"></span> 备份中...';

    try {
        const data = await api.post('/settings/database/backup');
        toast.success(`备份成功: ${data.backup_path}`);
    } catch (error) {
        toast.error('备份失败: ' + error.message);
    } finally {
        elements.backupBtn.disabled = false;
        elements.backupBtn.textContent = '💾 备份数据库';
    }
}

// 清理数据
async function handleCleanup() {
    const confirmed = await confirm('确定要清理过期数据吗？此操作不可恢复。');
    if (!confirmed) return;

    elements.cleanupBtn.disabled = true;
    elements.cleanupBtn.innerHTML = '<span class="loading-spinner"></span> 清理中...';

    try {
        const data = await api.post('/settings/database/cleanup?days=30');
        toast.success(data.message);
        loadDatabaseInfo();
    } catch (error) {
        toast.error('清理失败: ' + error.message);
    } finally {
        elements.cleanupBtn.disabled = false;
        elements.cleanupBtn.textContent = '🧹 清理过期数据';
    }
}

// 加载服务配置字段
async function loadServiceConfigFields(serviceType) {
    try {
        const data = await api.get('/email-services/types');
        const typeInfo = data.types.find(t => t.value === serviceType);

        if (!typeInfo) {
            elements.serviceConfigFields.innerHTML = '';
            return;
        }

        elements.serviceConfigFields.innerHTML = typeInfo.config_fields.map(field => `
            <div class="form-group">
                <label for="config-${field.name}">${field.label}</label>
                <input type="${field.name.includes('password') || field.name.includes('token') ? 'password' : 'text'}"
                       id="config-${field.name}"
                       name="${field.name}"
                       value="${field.default || ''}"
                       placeholder="${field.label}"
                       ${field.required ? 'required' : ''}>
            </div>
        `).join('');

    } catch (error) {
        console.error('加载配置字段失败:', error);
    }
}

// 添加邮箱服务
async function handleAddService(e) {
    e.preventDefault();

    const formData = new FormData(elements.addServiceForm);
    const config = {};

    elements.serviceConfigFields.querySelectorAll('input').forEach(input => {
        config[input.name] = input.value;
    });

    const data = {
        service_type: formData.get('service_type'),
        name: formData.get('name'),
        config: config,
        enabled: true,
        priority: 0,
    };

    try {
        await api.post('/email-services', data);
        toast.success('邮箱服务已添加');
        elements.addServiceModal.classList.remove('active');
        elements.addServiceForm.reset();
        loadEmailServices();
    } catch (error) {
        toast.error('添加失败: ' + error.message);
    }
}

// 测试服务
async function testService(id) {
    try {
        const data = await api.post(`/email-services/${id}/test`);
        if (data.success) {
            toast.success('服务连接正常');
        } else {
            toast.warning('服务连接失败: ' + data.message);
        }
    } catch (error) {
        toast.error('测试失败: ' + error.message);
    }
}

// 切换服务状态
async function toggleService(id, enabled) {
    try {
        const endpoint = enabled ? 'enable' : 'disable';
        await api.post(`/email-services/${id}/${endpoint}`);
        toast.success(enabled ? '服务已启用' : '服务已禁用');
        loadEmailServices();
    } catch (error) {
        toast.error('操作失败: ' + error.message);
    }
}

// 删除服务
async function deleteService(id) {
    const confirmed = await confirm('确定要删除此邮箱服务配置吗？');
    if (!confirmed) return;

    try {
        await api.delete(`/email-services/${id}`);
        toast.success('服务已删除');
        loadEmailServices();
    } catch (error) {
        toast.error('删除失败: ' + error.message);
    }
}

// 更新选中的服务
function updateSelectedServices() {
    selectedServiceIds.clear();
    document.querySelectorAll('.service-checkbox:checked').forEach(cb => {
        selectedServiceIds.add(parseInt(cb.dataset.id));
    });
}

// Outlook 批量导入
async function handleOutlookBatchImport() {
    const data = elements.outlookImportData.value.trim();
    if (!data) {
        toast.warning('请输入要导入的数据');
        return;
    }

    const enabled = document.getElementById('outlook-import-enabled').checked;
    const priority = parseInt(document.getElementById('outlook-import-priority').value) || 0;

    // 解析数据
    const lines = data.split('\n').filter(line => line.trim() && !line.trim().startsWith('#'));
    const accounts = [];
    const errors = [];

    lines.forEach((line, index) => {
        const parts = line.split('----').map(p => p.trim());
        if (parts.length < 2) {
            errors.push(`第 ${index + 1} 行格式错误`);
            return;
        }

        const account = {
            email: parts[0],
            password: parts[1],
            client_id: parts[2] || null,
            refresh_token: parts[3] || null,
            enabled: enabled,
            priority: priority
        };

        if (!account.email.includes('@')) {
            errors.push(`第 ${index + 1} 行邮箱格式错误: ${account.email}`);
            return;
        }

        accounts.push(account);
    });

    if (errors.length > 0) {
        elements.importResult.style.display = 'block';
        elements.importResult.innerHTML = `
            <div class="import-errors">${errors.map(e => `<div>${e}</div>`).join('')}</div>
        `;
        return;
    }

    elements.outlookImportBtn.disabled = true;
    elements.outlookImportBtn.innerHTML = '<span class="loading-spinner"></span> 导入中...';

    let successCount = 0;
    let failCount = 0;

    try {
        for (const account of accounts) {
            try {
                await api.post('/email-services', {
                    service_type: 'outlook',
                    name: account.email,
                    config: {
                        email: account.email,
                        password: account.password,
                        client_id: account.client_id,
                        refresh_token: account.refresh_token
                    },
                    enabled: account.enabled,
                    priority: account.priority
                });
                successCount++;
            } catch {
                failCount++;
            }
        }

        elements.importResult.style.display = 'block';
        elements.importResult.innerHTML = `
            <div class="import-stats">
                <span>✅ 成功: ${successCount}</span>
                <span>❌ 失败: ${failCount}</span>
            </div>
        `;

        toast.success(`导入完成，成功 ${successCount} 个`);
        loadEmailServices();

    } catch (error) {
        toast.error('导入失败: ' + error.message);
    } finally {
        elements.outlookImportBtn.disabled = false;
        elements.outlookImportBtn.textContent = '📥 开始导入';
    }
}

// HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}


// ============================================================================
// 代理列表管理
// ============================================================================

// 加载代理列表
async function loadProxies() {
    try {
        const data = await api.get('/settings/proxies');
        renderProxies(data.proxies);
        populatePreferredFixedProxyOptions(data.proxies || []);
    } catch (error) {
        console.error('加载代理列表失败:', error);
        populatePreferredFixedProxyOptions([]);
        elements.proxiesTable.innerHTML = `
            <tr>
                <td colspan="7">
                    <div class="empty-state">
                        <div class="empty-state-icon">❌</div>
                        <div class="empty-state-title">加载失败</div>
                    </div>
                </td>
            </tr>
        `;
    }
}

// 渲染代理列表
function renderProxies(proxies) {
    if (!proxies || proxies.length === 0) {
        elements.proxiesTable.innerHTML = `
            <tr>
                <td colspan="7">
                    <div class="empty-state">
                        <div class="empty-state-icon">🌐</div>
                        <div class="empty-state-title">暂无代理</div>
                        <div class="empty-state-description">点击"添加代理"按钮添加代理服务器</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    elements.proxiesTable.innerHTML = proxies.map(proxy => `
        <tr data-proxy-id="${proxy.id}">
            <td>${proxy.id}</td>
            <td>${escapeHtml(proxy.name)}</td>
            <td><span class="badge">${proxy.type.toUpperCase()}</span></td>
            <td><code>${escapeHtml(proxy.host)}:${proxy.port}</code></td>
            <td>
                ${proxy.is_default
                    ? '<span class="status-badge active">默认</span>'
                    : `<button class="btn btn-ghost btn-sm" onclick="handleSetProxyDefault(${proxy.id})" title="设为默认">设默认</button>`
                }
            </td>
            <td title="${proxy.enabled ? '已启用' : '已禁用'}">${proxy.enabled ? '✅' : '⭕'}</td>
            <td>${format.date(proxy.last_used)}</td>
            <td>
                <div style="display:flex;gap:4px;align-items:center;white-space:nowrap;">
                    <button class="btn btn-secondary btn-sm" onclick="editProxyItem(${proxy.id})">编辑</button>
                    <div class="dropdown" style="position:relative;">
                        <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();toggleSettingsMoreMenu(this)">更多</button>
                        <div class="dropdown-menu" style="min-width:80px;">
                            <a href="#" class="dropdown-item" onclick="event.preventDefault();closeSettingsMoreMenu(this);testProxyItem(${proxy.id})">测试</a>
                            <a href="#" class="dropdown-item" onclick="event.preventDefault();closeSettingsMoreMenu(this);toggleProxyItem(${proxy.id}, ${!proxy.enabled})">${proxy.enabled ? '禁用' : '启用'}</a>
                            ${!proxy.is_default ? `<a href="#" class="dropdown-item" onclick="event.preventDefault();closeSettingsMoreMenu(this);handleSetProxyDefault(${proxy.id})">设为默认</a>` : ''}
                        </div>
                    </div>
                    <button class="btn btn-danger btn-sm" onclick="deleteProxyItem(${proxy.id})">删除</button>
                </div>
            </td>
        </tr>
    `).join('');
}

function populatePreferredFixedProxyOptions(proxies) {
    const select = document.getElementById('proxy-preferred-fixed-id');
    if (!select) return;
    const currentValue = select.dataset.preferredValue || select.value || '0';
    const enabledProxies = (proxies || []).filter(proxy => proxy && proxy.enabled);
    select.innerHTML = [
        '<option value="0">请选择固定代理</option>',
        ...enabledProxies.map(proxy => {
            const label = `${escapeHtml(proxy.name)} (#${proxy.id}) - ${escapeHtml(proxy.host)}:${proxy.port}${proxy.is_default ? ' [默认]' : ''}`;
            return `<option value="${proxy.id}">${label}</option>`;
        })
    ].join('');
    const hasCurrent = enabledProxies.some(proxy => String(proxy.id) === String(currentValue));
    select.value = hasCurrent ? String(currentValue) : '0';
}

function toggleSettingsMoreMenu(btn) {
    const menu = btn.nextElementSibling;
    const isActive = menu.classList.contains('active');
    document.querySelectorAll('.dropdown-menu.active').forEach(m => m.classList.remove('active'));
    if (!isActive) menu.classList.add('active');
}

function closeSettingsMoreMenu(el) {
    const menu = el.closest('.dropdown-menu');
    if (menu) menu.classList.remove('active');
}

// 设为默认代理
async function handleSetProxyDefault(id) {
    try {
        await api.post(`/settings/proxies/${id}/set-default`);
        toast.success('已设为默认代理');
        loadProxies();
    } catch (error) {
        toast.error('操作失败: ' + error.message);
    }
}

// 打开代理模态框
function openProxyModal(proxy = null) {
    elements.proxyModalTitle.textContent = proxy ? '编辑代理' : '添加代理';
    elements.proxyItemForm.reset();

    document.getElementById('proxy-item-id').value = proxy ? proxy.id : '';

    if (proxy) {
        document.getElementById('proxy-item-name').value = proxy.name || '';
        document.getElementById('proxy-item-type').value = proxy.type || 'http';
        document.getElementById('proxy-item-host').value = proxy.host || '';
        document.getElementById('proxy-item-port').value = proxy.port || '';
        document.getElementById('proxy-item-username').value = proxy.username || '';
        document.getElementById('proxy-item-password').value = '';
    }

    elements.addProxyModal.classList.add('active');
}

// 关闭代理模态框
function closeProxyModal() {
    elements.addProxyModal.classList.remove('active');
    elements.proxyItemForm.reset();
}

// 保存代理
async function handleSaveProxyItem(e) {
    e.preventDefault();

    const proxyId = document.getElementById('proxy-item-id').value;
    const data = {
        name: document.getElementById('proxy-item-name').value,
        type: document.getElementById('proxy-item-type').value,
        host: document.getElementById('proxy-item-host').value,
        port: parseInt(document.getElementById('proxy-item-port').value),
        username: document.getElementById('proxy-item-username').value || null,
        password: document.getElementById('proxy-item-password').value || null,
        enabled: true
    };

    try {
        if (proxyId) {
            await api.patch(`/settings/proxies/${proxyId}`, data);
            toast.success('代理已更新');
        } else {
            await api.post('/settings/proxies', data);
            toast.success('代理已添加');
        }
        closeProxyModal();
        loadProxies();
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

// 编辑代理
async function editProxyItem(id) {
    try {
        const proxy = await api.get(`/settings/proxies/${id}`);
        openProxyModal(proxy);
    } catch (error) {
        toast.error('获取代理信息失败');
    }
}

// 测试单个代理
async function testProxyItem(id) {
    try {
        const result = await api.post(`/settings/proxies/${id}/test`);
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (error) {
        toast.error('测试失败: ' + error.message);
    }
}

// 切换代理状态
async function toggleProxyItem(id, enabled) {
    try {
        const endpoint = enabled ? 'enable' : 'disable';
        await api.post(`/settings/proxies/${id}/${endpoint}`);
        toast.success(enabled ? '代理已启用' : '代理已禁用');
        loadProxies();
    } catch (error) {
        toast.error('操作失败: ' + error.message);
    }
}

// 删除代理
async function deleteProxyItem(id) {
    const confirmed = await confirm('确定要删除此代理吗？');
    if (!confirmed) return;

    try {
        await api.delete(`/settings/proxies/${id}`);
        toast.success('代理已删除');
        loadProxies();
    } catch (error) {
        toast.error('删除失败: ' + error.message);
    }
}

// 测试所有代理
async function handleTestAllProxies() {
    elements.testAllProxiesBtn.disabled = true;
    elements.testAllProxiesBtn.innerHTML = '<span class="loading-spinner"></span> 测试中...';

    try {
        const result = await api.post('/settings/proxies/test-all');
        toast.info(`测试完成: 成功 ${result.success}, 失败 ${result.failed}`);
        loadProxies();
    } catch (error) {
        toast.error('测试失败: ' + error.message);
    } finally {
        elements.testAllProxiesBtn.disabled = false;
        elements.testAllProxiesBtn.textContent = '🔌 测试全部';
    }
}


// ============================================================================
// Outlook 设置管理
// ============================================================================

// 加载 Outlook 设置
async function loadOutlookSettings() {
    try {
        const data = await api.get('/settings/outlook');
        const el = document.getElementById('outlook-default-client-id');
        if (el) el.value = data.default_client_id || '';
    } catch (error) {
        console.error('加载 Outlook 设置失败:', error);
    }
}

// 保存 Outlook 设置
async function handleSaveOutlookSettings(e) {
    e.preventDefault();
    const data = {
        default_client_id: document.getElementById('outlook-default-client-id').value
    };
    try {
        await api.post('/settings/outlook', data);
        toast.success('Outlook 设置已保存');
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

// ============== 动态代理设置 ==============

function collectDynamicProxyPayload({ includeSecrets = true, includeOperationFlags = true } = {}) {
    const mode = document.getElementById('dynamic-proxy-mode').value || 'api';
    const provider = document.getElementById('dynamic-proxy-provider').value || 'generic';
    const payload = {
        enabled: document.getElementById('dynamic-proxy-enabled').checked,
        mode,
        provider,
        refresh_use_proxy: includeOperationFlags ? !!document.getElementById('proxy-refresh-use-proxy')?.checked : false,
        validate_use_proxy: includeOperationFlags ? !!document.getElementById('proxy-validate-use-proxy')?.checked : false,
    };

    if (mode === 'account') {
        payload.scheme = document.getElementById('dynamic-proxy-scheme').value || 'http';
        payload.host = document.getElementById('dynamic-proxy-host').value.trim();
        payload.port = parseInt(document.getElementById('dynamic-proxy-port').value) || 1456;
        payload.username = document.getElementById('dynamic-proxy-username').value.trim();
        payload.password = includeSecrets ? (document.getElementById('dynamic-proxy-password').value || null) : null;
        payload.country = document.getElementById('dynamic-proxy-country').value.trim() || 'us';
        payload.api_url = '';
        payload.api_key = null;
        payload.api_key_header = 'X-API-Key';
        payload.result_field = '';
        payload.provider_appid = '';
        payload.provider_appkey = null;
        payload.seekproxy_trade_no = '';
        payload.seekproxy_key = null;
        payload.seekproxy_auth_type = 2;
        payload.seekproxy_ip_count = 1;
        payload.seekproxy_state = '';
        payload.seekproxy_city = '';
        payload.seekproxy_break_type = 1;
        payload.seekproxy_time = 5;
        return payload;
    }

    if (provider === 'seekproxy') {
        payload.api_url = '';
        payload.api_key = null;
        payload.api_key_header = 'X-API-Key';
        payload.result_field = '';
        payload.provider_appid = '';
        payload.provider_appkey = null;
        payload.seekproxy_trade_no = document.getElementById('dynamic-proxy-seekproxy-trade-no').value.trim();
        payload.seekproxy_key = includeSecrets ? (document.getElementById('dynamic-proxy-seekproxy-key').value || null) : null;
        payload.seekproxy_auth_type = parseInt(document.getElementById('dynamic-proxy-seekproxy-auth-type').value) || 2;
        payload.seekproxy_ip_count = parseInt(document.getElementById('dynamic-proxy-seekproxy-ip-count').value) || 1;
        payload.seekproxy_protocol = parseInt(document.getElementById('dynamic-proxy-seekproxy-protocol').value) || 0;
        payload.seekproxy_pattern = parseInt(document.getElementById('dynamic-proxy-seekproxy-pattern').value) || 0;
        payload.seekproxy_valid_code = parseInt(document.getElementById('dynamic-proxy-seekproxy-valid-code').value) || 0;
        payload.seekproxy_state = document.getElementById('dynamic-proxy-seekproxy-state').value.trim();
        payload.seekproxy_city = document.getElementById('dynamic-proxy-seekproxy-city').value.trim();
        payload.seekproxy_break_type = parseInt(document.getElementById('dynamic-proxy-seekproxy-break-type').value) || 1;
        payload.seekproxy_time = parseInt(document.getElementById('dynamic-proxy-seekproxy-time').value) || 5;
        payload.scheme = 'http';
        payload.host = '';
        payload.port = 1456;
        payload.username = '';
        payload.password = null;
        payload.country = document.getElementById('dynamic-proxy-seekproxy-country').value.trim() || 'US';
        return payload;
    }

    payload.api_url = document.getElementById('dynamic-proxy-api-url').value.trim();
    payload.api_key = includeSecrets ? (document.getElementById('dynamic-proxy-api-key').value || null) : null;
    payload.api_key_header = document.getElementById('dynamic-proxy-api-key-header').value.trim() || 'X-API-Key';
    payload.result_field = document.getElementById('dynamic-proxy-result-field').value.trim();
    payload.provider_appid = provider === 'haiwaidaili' ? document.getElementById('dynamic-proxy-provider-appid').value.trim() : '';
    payload.provider_appkey = provider === 'haiwaidaili' && includeSecrets ? (document.getElementById('dynamic-proxy-provider-appkey').value || null) : null;
    payload.seekproxy_trade_no = '';
    payload.seekproxy_key = null;
    payload.seekproxy_auth_type = 2;
    payload.seekproxy_ip_count = 1;
    payload.seekproxy_protocol = 0;
    payload.seekproxy_pattern = 0;
    payload.seekproxy_valid_code = 0;
    payload.seekproxy_state = '';
    payload.seekproxy_city = '';
    payload.seekproxy_break_type = 1;
    payload.seekproxy_time = 5;
    payload.scheme = document.getElementById('dynamic-proxy-scheme').value || 'http';
    payload.host = '';
    payload.port = 1456;
    payload.username = '';
    payload.password = null;
    payload.country = document.getElementById('dynamic-proxy-country').value.trim() || 'us';
    return payload;
}

async function handleSaveProxyOperationSettings(e) {
    e.preventDefault();
    const payload = collectDynamicProxyPayload({ includeSecrets: false, includeOperationFlags: true });
    try {
        await api.post('/settings/proxy/dynamic', payload);
        toast.success('刷新/验证代理开关已保存');
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

async function handleSaveProxyPreference(e) {
    e.preventDefault();
    const mode = document.getElementById('proxy-preference-mode').value || 'auto';
    const preferredFixedId = parseInt(document.getElementById('proxy-preferred-fixed-id').value) || 0;
    const connectRetryCount = parseInt(document.getElementById('proxy-connect-retry-count').value) || 3;
    try {
        await api.post('/settings/proxy/preference', {
            preference_mode: mode,
            preferred_fixed_id: preferredFixedId,
            connect_retry_count: connectRetryCount,
        });
        toast.success('任务代理策略已保存');
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

async function handleTestProxyPreference() {
    const btn = elements.testProxyPreferenceBtn;
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '测试中...';
    try {
        const mode = document.getElementById('proxy-preference-mode').value || 'auto';
        const preferredFixedId = parseInt(document.getElementById('proxy-preferred-fixed-id').value) || 0;
        const connectRetryCount = parseInt(document.getElementById('proxy-connect-retry-count').value) || 3;
        const result = await api.post('/settings/proxy/preference/test', {
            preference_mode: mode,
            preferred_fixed_id: preferredFixedId,
            connect_retry_count: connectRetryCount,
        });
        const sourceText = result.proxy_source_name ? `来源: ${result.proxy_source_name}` : '来源: -';
        const proxyText = result.proxy_used ? `
代理: ${result.proxy_used}` : '';
        const httpsText = result.https_openai_message
            ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
            : '';
        const message = `${sourceText}${proxyText}
${result.message || (result.success ? '代理可用' : '代理不可用')}${httpsText}`;
        if (result.success) {
            toast.success(message);
        } else {
            toast.error(message);
        }
    } catch (error) {
        toast.error('测试失败: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '🔌 测试当前策略代理';
    }
}

async function handleSaveDynamicProxy(e) {
    e.preventDefault();
    const data = collectDynamicProxyPayload({ includeSecrets: true, includeOperationFlags: true });
    try {
        await api.post('/settings/proxy/dynamic', data);
        toast.success('动态代理设置已保存');
        const dynamicKeyInput = document.getElementById('dynamic-proxy-api-key');
        const dynamicKeyStatus = document.getElementById('dynamic-proxy-api-key-status');
        if (dynamicKeyInput) {
            const hasKey = Boolean(data.api_key) || dynamicKeyInput.dataset.hasKey === '1';
            dynamicKeyInput.value = '';
            dynamicKeyInput.dataset.hasKey = hasKey ? '1' : '0';
            dynamicKeyInput.placeholder = hasKey ? '已配置，留空保持不变' : '留空保持不变';
            if (dynamicKeyStatus) {
                dynamicKeyStatus.textContent = hasKey ? '已保存 API Key' : '未保存 API Key';
            }
        }
        const providerAppkeyInput = document.getElementById('dynamic-proxy-provider-appkey');
        const providerAppkeyStatus = document.getElementById('dynamic-proxy-provider-appkey-status');
        if (providerAppkeyInput) {
            const hasProviderAppkey = Boolean(data.provider_appkey) || providerAppkeyInput.dataset.hasKey === '1';
            providerAppkeyInput.value = '';
            providerAppkeyInput.dataset.hasKey = hasProviderAppkey ? '1' : '0';
            providerAppkeyInput.placeholder = hasProviderAppkey ? '已配置，留空保持不变' : '请输入 AppKey';
            if (providerAppkeyStatus) {
                providerAppkeyStatus.textContent = hasProviderAppkey ? '已保存 AppKey' : '未保存 AppKey';
            }
        }
        const seekproxyKeyInput = document.getElementById('dynamic-proxy-seekproxy-key');
        const seekproxyKeyStatus = document.getElementById('dynamic-proxy-seekproxy-key-status');
        if (seekproxyKeyInput) {
            const hasSeekproxyKey = Boolean(data.seekproxy_key) || seekproxyKeyInput.dataset.hasKey === '1';
            seekproxyKeyInput.value = '';
            seekproxyKeyInput.dataset.hasKey = hasSeekproxyKey ? '1' : '0';
            seekproxyKeyInput.placeholder = hasSeekproxyKey ? '已配置，留空保持不变' : '请输入 SeekProxy key';
            if (seekproxyKeyStatus) {
                seekproxyKeyStatus.textContent = hasSeekproxyKey ? '已保存 key' : '未保存 key';
            }
        }
        const dynamicPasswordInput = document.getElementById('dynamic-proxy-password');
        const dynamicPasswordStatus = document.getElementById('dynamic-proxy-password-status');
        if (dynamicPasswordInput) {
            const hasPassword = Boolean(data.password) || dynamicPasswordInput.dataset.hasPassword === '1';
            dynamicPasswordInput.value = '';
            dynamicPasswordInput.dataset.hasPassword = hasPassword ? '1' : '0';
            dynamicPasswordInput.placeholder = hasPassword ? '已配置，留空保持不变' : '请输入代理密码';
            if (dynamicPasswordStatus) {
                dynamicPasswordStatus.textContent = hasPassword ? '已保存代理密码' : '未保存代理密码';
            }
        }

        const profileKey = `${data.provider || 'generic'}::${data.mode || 'api'}`;
        const currentProfile = dynamicProxyProfiles[profileKey] || {};
        dynamicProxyProfiles[profileKey] = {
            ...currentProfile,
            ...data,
            trade_no: data.seekproxy_trade_no ?? currentProfile.trade_no,
            auth_type: data.seekproxy_auth_type ?? currentProfile.auth_type,
            ip_count: data.seekproxy_ip_count ?? currentProfile.ip_count,
            state: data.seekproxy_state ?? currentProfile.state,
            city: data.seekproxy_city ?? currentProfile.city,
            break_type: data.seekproxy_break_type ?? currentProfile.break_type,
            time: data.seekproxy_time ?? currentProfile.time,
            protocol: data.seekproxy_protocol ?? currentProfile.protocol,
            pattern: data.seekproxy_pattern ?? currentProfile.pattern,
            valid_code: data.seekproxy_valid_code ?? currentProfile.valid_code,
            provider_appid: data.provider_appid ?? currentProfile.provider_appid,
            api_url: data.api_url ?? currentProfile.api_url,
            api_key_header: data.api_key_header ?? currentProfile.api_key_header,
            result_field: data.result_field ?? currentProfile.result_field,
            password: undefined,
            api_key: undefined,
            provider_appkey: undefined,
            seekproxy_key: undefined
        };
    } catch (error) {
        toast.error('保存失败: ' + error.message);
    }
}

async function handleTestDynamicProxy() {
    const payload = collectDynamicProxyPayload({ includeSecrets: true, includeOperationFlags: true });
    if (payload.mode === 'api' && payload.provider === 'generic' && !payload.api_url) {
        toast.warning('请先填写动态代理 API 地址');
        return;
    }
    if (payload.mode === 'api' && payload.provider === 'haiwaidaili' && !payload.api_url) {
        toast.warning('请先填写海外代理 API 地址');
        return;
    }
    const btn = elements.testDynamicProxyBtn;
    btn.disabled = true;
    btn.textContent = '测试中...';
    try {
        const result = await api.post('/settings/proxy/dynamic/test', payload);
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            const white = result.whitelist_message ? `\n白名单: ${result.whitelist_message}` : '';
            toast.success(`${result.message}${white}${extra}`);
        } else {
            const white = result.whitelist_message ? `\n白名单: ${result.whitelist_message}` : '';
            toast.error(`${result.message}${white}`);
        }
    } catch (error) {
        toast.error('测试失败: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '🔌 测试动态代理';
    }
}

function updateDynamicProxyModeUi() {
    const mode = document.getElementById('dynamic-proxy-mode')?.value || 'api';
    const provider = document.getElementById('dynamic-proxy-provider')?.value || 'generic';
    const currentProfileKey = getDynamicProfileKey();
    if (lastDynamicProxyProfileKey && lastDynamicProxyProfileKey !== currentProfileKey) {
        dynamicProxyProfiles[lastDynamicProxyProfileKey] = {
            ...(dynamicProxyProfiles[lastDynamicProxyProfileKey] || {}),
            ...collectDynamicProxyPayload({ includeSecrets: false, includeOperationFlags: true }),
            trade_no: document.getElementById('dynamic-proxy-seekproxy-trade-no')?.value?.trim() || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.trade_no || '',
            auth_type: parseInt(document.getElementById('dynamic-proxy-seekproxy-auth-type')?.value || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.auth_type || '2', 10) || 2,
            ip_count: parseInt(document.getElementById('dynamic-proxy-seekproxy-ip-count')?.value || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.ip_count || '1', 10) || 1,
            state: document.getElementById('dynamic-proxy-seekproxy-state')?.value?.trim() || '',
            city: document.getElementById('dynamic-proxy-seekproxy-city')?.value?.trim() || '',
            break_type: parseInt(document.getElementById('dynamic-proxy-seekproxy-break-type')?.value || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.break_type || '1', 10) || 1,
            time: parseInt(document.getElementById('dynamic-proxy-seekproxy-time')?.value || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.time || '5', 10) || 5,
            protocol: parseInt(document.getElementById('dynamic-proxy-seekproxy-protocol')?.value || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.protocol || '0', 10) || 0,
            pattern: parseInt(document.getElementById('dynamic-proxy-seekproxy-pattern')?.value || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.pattern || '0', 10) || 0,
            valid_code: parseInt(document.getElementById('dynamic-proxy-seekproxy-valid-code')?.value || dynamicProxyProfiles[lastDynamicProxyProfileKey]?.valid_code || '0', 10) || 0,
        };
    }
    lastDynamicProxyProfileKey = currentProfileKey;
    const summary = document.getElementById('dynamic-proxy-platform-summary');
    const summaryMap = {
        haiwaidaili: {
            api: '海外代理 · API提取模式：使用代理 API 地址提取节点，可选填写 AppId/AppKey 自动检查白名单。',
            account: '海外代理 · 账密接入模式：直连代理网关，需填写代理主机、端口、用户名、密码和国家代码。',
        },
        seekproxy: {
            api: 'SeekProxy · API提取模式：使用 trade_no / key 提取代理，系统会自动解析 host:port:user:pass 并从多节点中选择可用节点。',
            account: 'SeekProxy · 账密接入模式：当前主要适配 API 提取；如你有官方账密网关资料，也可在此模式下填写主机、端口、用户名、密码直连。',
        },
        generic: {
            api: '通用 API 提取模式：手动填写代理 API 地址，系统按通用文本/JSON 规则提取代理 URL。',
            account: '通用账密接入模式：填写标准代理主机、端口、用户名、密码后直连。',
        },
    };
    if (summary) {
        summary.textContent = summaryMap[provider]?.[mode] || '请选择代理平台与接入模式。';
    }
    const genericApiIds = [
        'dynamic-proxy-api-mode-group',
        'dynamic-proxy-api-key-group',
        'dynamic-proxy-api-key-header-group',
        'dynamic-proxy-result-field-group',
    ];
    genericApiIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = mode === 'api' && provider === 'generic' ? '' : 'none';
    });
    const seekproxyGroup = document.getElementById('dynamic-proxy-seekproxy-group');
    if (seekproxyGroup) seekproxyGroup.style.display = mode === 'api' && provider === 'seekproxy' ? '' : 'none';
    const seekproxyAuth1Group = document.getElementById('dynamic-proxy-seekproxy-auth1-group');
    const seekproxyAuthType = parseInt(document.getElementById('dynamic-proxy-seekproxy-auth-type')?.value || '2', 10) || 2;
    if (seekproxyAuth1Group) seekproxyAuth1Group.style.display = mode === 'api' && provider === 'seekproxy' && seekproxyAuthType === 1 ? '' : 'none';
    const haiwaidailiGroup = document.getElementById('dynamic-proxy-provider-auth-group');
    if (haiwaidailiGroup) haiwaidailiGroup.style.display = mode === 'api' && provider === 'haiwaidaili' ? '' : 'none';
    const accountGroup = document.getElementById('dynamic-proxy-account-mode-group');
    if (accountGroup) accountGroup.style.display = mode === 'account' ? '' : 'none';
    applyDynamicProfile(normalizeSeekproxyProfileSnapshot(dynamicProxyProfiles[currentProfileKey] || {}));
    if (mode === 'api' && provider === 'seekproxy') {
        handleSeekproxyCountrySearch().catch(() => {});
        handleSeekproxyCountryCodeChanged().catch(() => {});
    }
}

function updateProxyPreferenceUi() {
    const mode = document.getElementById('proxy-preference-mode')?.value || 'auto';
    const group = document.getElementById('proxy-preferred-fixed-group');
    if (group) group.style.display = mode === 'fixed' ? '' : 'none';
}

function getDynamicProfileKey() {
    const provider = document.getElementById('dynamic-proxy-provider')?.value || 'haiwaidaili';
    const mode = document.getElementById('dynamic-proxy-mode')?.value || 'api';
    return `${provider}::${mode}`;
}

function setSelectOptions(selectEl, items, valueKey = 'code', labelBuilder = (item) => item.name || item.code || '') {
    if (!selectEl) return;
    const rows = Array.isArray(items) ? items : [];
    selectEl.innerHTML = `<option value="">留空随机</option>` + rows.map(item => {
        const value = item?.[valueKey] ?? item?.code ?? item?.name ?? '';
        const label = labelBuilder(item);
        return `<option value="${escapeHtml(String(value))}">${escapeHtml(String(label || value))}</option>`;
    }).join('');
}

function renderSeekproxyCountryCandidates(items) {
    const input = document.getElementById('dynamic-proxy-seekproxy-country-search');
    if (!input) return;
    const listId = 'dynamic-proxy-seekproxy-country-list';
    let list = document.getElementById(listId);
    if (!list) {
        list = document.createElement('datalist');
        list.id = listId;
        document.body.appendChild(list);
        input.setAttribute('list', listId);
    }
    const rows = Array.isArray(items) ? items : [];
    list.innerHTML = rows.map(item => {
        const code = String(item.code || '').toUpperCase();
        const name = String(item.name || '');
        return `<option value="${escapeHtml(code)}">${escapeHtml(name ? `${code} - ${name}` : code)}</option>`;
    }).join('');
    setSelectOptions(
        document.getElementById('dynamic-proxy-seekproxy-country-options'),
        rows,
        'code',
        (item) => `${String(item.code || '').toUpperCase()} - ${item.name || ''}`
    );
}

async function loadSeekproxyCountries(keyword = '') {
    const result = await api.get(`/settings/proxy/seekproxy/countries?keyword=${encodeURIComponent(keyword)}`);
    seekproxyGeoCache.countries = result.items || [];
    return seekproxyGeoCache.countries;
}

async function loadSeekproxyStates(countryCode, keyword = '') {
    if (!countryCode) {
        seekproxyGeoCache.states[countryCode || ''] = [];
        return [];
    }
    const result = await api.get(`/settings/proxy/seekproxy/states?country_code=${encodeURIComponent(countryCode)}&keyword=${encodeURIComponent(keyword)}`);
    seekproxyGeoCache.states[countryCode.toUpperCase()] = result.items || [];
    return result.items || [];
}

async function loadSeekproxyCities(countryCode, state, keyword = '') {
    if (!countryCode || !state) {
        return [];
    }
    const result = await api.get(`/settings/proxy/seekproxy/cities?country_code=${encodeURIComponent(countryCode)}&state=${encodeURIComponent(state)}&keyword=${encodeURIComponent(keyword)}`);
    const cacheKey = `${countryCode.toUpperCase()}::${state}`;
    seekproxyGeoCache.cities[cacheKey] = result.items || [];
    return result.items || [];
}

async function handleSeekproxyCountrySearch() {
    try {
        const keyword = document.getElementById('dynamic-proxy-seekproxy-country-search')?.value?.trim() || '';
        const items = await loadSeekproxyCountries(keyword);
        renderSeekproxyCountryCandidates(items);
        const stateOptions = document.getElementById('dynamic-proxy-seekproxy-state-options');
        const cityOptions = document.getElementById('dynamic-proxy-seekproxy-city-options');
        setSelectOptions(stateOptions, []);
        setSelectOptions(cityOptions, []);
        if (items.length === 1) {
            document.getElementById('dynamic-proxy-seekproxy-country').value = items[0].code || '';
            await handleSeekproxyCountryCodeChanged();
        }
    } catch (error) {
        toast.error('加载 SeekProxy 国家失败: ' + error.message);
    }
}

async function handleSeekproxyCountrySelected() {
    const optionValue = document.getElementById('dynamic-proxy-seekproxy-country-options')?.value || '';
    if (!optionValue) return;
    const countryInput = document.getElementById('dynamic-proxy-seekproxy-country');
    const searchInput = document.getElementById('dynamic-proxy-seekproxy-country-search');
    if (countryInput) countryInput.value = optionValue.toUpperCase();
    if (searchInput) {
        const matched = (seekproxyGeoCache.countries || []).find(item => String(item.code || '').toUpperCase() === optionValue.toUpperCase());
        searchInput.value = matched ? `${matched.code} - ${matched.name}` : optionValue.toUpperCase();
    }
    await handleSeekproxyCountryCodeChanged();
}

async function handleSeekproxyCountryCodeChanged() {
    const countryInput = document.getElementById('dynamic-proxy-seekproxy-country');
    const searchInput = document.getElementById('dynamic-proxy-seekproxy-country-search');
    let countryCode = countryInput?.value?.trim()?.toUpperCase() || '';
    if (!countryCode && searchInput?.value?.trim()) {
        const keyword = searchInput.value.trim().toLowerCase();
        const matched = (seekproxyGeoCache.countries || []).find(item => {
            const code = String(item.code || '').toLowerCase();
            const name = String(item.name || '').toLowerCase();
            return keyword === code || keyword === name || `${code} - ${name}` === keyword;
        });
        if (matched) {
            countryCode = String(matched.code || '').toUpperCase();
            if (countryInput) countryInput.value = countryCode;
        }
    }
    const stateInput = document.getElementById('dynamic-proxy-seekproxy-state');
    const cityInput = document.getElementById('dynamic-proxy-seekproxy-city');
    if (stateInput) stateInput.value = '';
    if (cityInput) cityInput.value = '';
    document.getElementById('dynamic-proxy-seekproxy-state-search').value = '';
    document.getElementById('dynamic-proxy-seekproxy-city-search').value = '';
    try {
        const items = await loadSeekproxyStates(countryCode, '');
        const searchInput = document.getElementById('dynamic-proxy-seekproxy-country-search');
        if (searchInput) {
            const matched = (seekproxyGeoCache.countries || []).find(item => String(item.code || '').toUpperCase() === countryCode);
            searchInput.value = matched ? `${matched.code} - ${matched.name}` : countryCode;
        }
        setSelectOptions(
            document.getElementById('dynamic-proxy-seekproxy-state-options'),
            items,
            'name',
            (item) => `${item.name || item.code}${item.code && item.code !== item.name ? ` (${item.code})` : ''}`
        );
        const stateInput = document.getElementById('dynamic-proxy-seekproxy-state');
        const stateOptions = document.getElementById('dynamic-proxy-seekproxy-state-options');
        if (stateOptions && stateInput?.value) {
            stateOptions.value = stateInput.value;
        }
        setSelectOptions(document.getElementById('dynamic-proxy-seekproxy-city-options'), []);
    } catch (error) {
        toast.error('加载 SeekProxy 州省失败: ' + error.message);
    }
}

function normalizeSeekproxyProfileSnapshot(profile) {
    const p = profile || {};
    return {
        ...p,
        trade_no: p.trade_no ?? p.seekproxy_trade_no ?? '',
        auth_type: p.auth_type ?? p.seekproxy_auth_type ?? 2,
        ip_count: p.ip_count ?? p.seekproxy_ip_count ?? 1,
        state: p.state ?? p.seekproxy_state ?? '',
        city: p.city ?? p.seekproxy_city ?? '',
        break_type: p.break_type ?? p.seekproxy_break_type ?? 1,
        time: p.time ?? p.seekproxy_time ?? 5,
        protocol: p.protocol ?? p.seekproxy_protocol ?? 0,
        pattern: p.pattern ?? p.seekproxy_pattern ?? 0,
        valid_code: p.valid_code ?? p.seekproxy_valid_code ?? 0,
    };
}

async function handleSeekproxyStateSearch() {
    const countryCode = document.getElementById('dynamic-proxy-seekproxy-country')?.value?.trim()?.toUpperCase() || '';
    const keyword = document.getElementById('dynamic-proxy-seekproxy-state-search')?.value?.trim() || '';
    try {
        const items = await loadSeekproxyStates(countryCode, keyword);
        setSelectOptions(
            document.getElementById('dynamic-proxy-seekproxy-state-options'),
            items,
            'name',
            (item) => `${item.name || item.code}${item.code && item.code !== item.name ? ` (${item.code})` : ''}`
        );
        const stateInput = document.getElementById('dynamic-proxy-seekproxy-state');
        const stateSearch = document.getElementById('dynamic-proxy-seekproxy-state-search');
        if (stateInput && keyword && !stateInput.value && items.length === 1) {
            stateInput.value = items[0].name || items[0].code || '';
            if (stateSearch) stateSearch.value = stateInput.value;
        }
        if (items.length === 1 && stateInput) {
            stateInput.value = items[0].name || items[0].code || '';
            if (stateSearch) stateSearch.value = stateInput.value;
        }
    } catch (error) {
        toast.error('搜索 SeekProxy 州省失败: ' + error.message);
    }
}

async function handleSeekproxyStateSelected() {
    const stateValue = document.getElementById('dynamic-proxy-seekproxy-state-options')?.value || '';
    const stateInput = document.getElementById('dynamic-proxy-seekproxy-state');
    const cityInput = document.getElementById('dynamic-proxy-seekproxy-city');
    if (stateInput) stateInput.value = stateValue;
    const stateSearch = document.getElementById('dynamic-proxy-seekproxy-state-search');
    if (stateSearch) stateSearch.value = stateValue;
    if (cityInput) cityInput.value = '';
    document.getElementById('dynamic-proxy-seekproxy-city-search').value = '';
    const countryCode = document.getElementById('dynamic-proxy-seekproxy-country')?.value?.trim()?.toUpperCase() || '';
    try {
        const items = await loadSeekproxyCities(countryCode, stateValue, '');
        setSelectOptions(
            document.getElementById('dynamic-proxy-seekproxy-city-options'),
            items,
            'name',
            (item) => `${item.name || item.code}${item.code && item.code !== item.name ? ` (${item.code})` : ''}`
        );
        const cityOptions = document.getElementById('dynamic-proxy-seekproxy-city-options');
        if (cityOptions && cityInput?.value) {
            cityOptions.value = cityInput.value;
        }
    } catch (error) {
        toast.error('加载 SeekProxy 城市失败: ' + error.message);
    }
}

async function handleSeekproxyCitySearch() {
    const countryCode = document.getElementById('dynamic-proxy-seekproxy-country')?.value?.trim()?.toUpperCase() || '';
    const stateValue = document.getElementById('dynamic-proxy-seekproxy-state')?.value?.trim() || '';
    const keyword = document.getElementById('dynamic-proxy-seekproxy-city-search')?.value?.trim() || '';
    try {
        const items = await loadSeekproxyCities(countryCode, stateValue, keyword);
        setSelectOptions(
            document.getElementById('dynamic-proxy-seekproxy-city-options'),
            items,
            'name',
            (item) => `${item.name || item.code}${item.code && item.code !== item.name ? ` (${item.code})` : ''}`
        );
        const cityInput = document.getElementById('dynamic-proxy-seekproxy-city');
        const citySearch = document.getElementById('dynamic-proxy-seekproxy-city-search');
        if (items.length === 1 && cityInput) {
            cityInput.value = items[0].name || items[0].code || '';
            if (citySearch) citySearch.value = cityInput.value;
        }
    } catch (error) {
        toast.error('搜索 SeekProxy 城市失败: ' + error.message);
    }
}

function handleSeekproxyCitySelected() {
    const cityValue = document.getElementById('dynamic-proxy-seekproxy-city-options')?.value || '';
    const cityInput = document.getElementById('dynamic-proxy-seekproxy-city');
    if (cityInput) cityInput.value = cityValue;
    const citySearch = document.getElementById('dynamic-proxy-seekproxy-city-search');
    if (citySearch) citySearch.value = cityValue;
}

function applyDynamicProfile(profile) {
    const p = normalizeSeekproxyProfileSnapshot(profile || {});
    if (document.getElementById('dynamic-proxy-api-url')) document.getElementById('dynamic-proxy-api-url').value = p.api_url || '';
    if (document.getElementById('dynamic-proxy-api-key-header')) document.getElementById('dynamic-proxy-api-key-header').value = p.api_key_header || 'X-API-Key';
    if (document.getElementById('dynamic-proxy-result-field')) document.getElementById('dynamic-proxy-result-field').value = p.result_field || '';
    if (document.getElementById('dynamic-proxy-provider-appid')) document.getElementById('dynamic-proxy-provider-appid').value = p.provider_appid || '';
    if (document.getElementById('dynamic-proxy-seekproxy-trade-no')) document.getElementById('dynamic-proxy-seekproxy-trade-no').value = p.trade_no || '';
    if (document.getElementById('dynamic-proxy-seekproxy-auth-type')) document.getElementById('dynamic-proxy-seekproxy-auth-type').value = p.auth_type || 2;
    if (document.getElementById('dynamic-proxy-seekproxy-ip-count')) document.getElementById('dynamic-proxy-seekproxy-ip-count').value = p.ip_count || 1;
    if (document.getElementById('dynamic-proxy-seekproxy-protocol')) document.getElementById('dynamic-proxy-seekproxy-protocol').value = p.protocol ?? 0;
    if (document.getElementById('dynamic-proxy-seekproxy-pattern')) document.getElementById('dynamic-proxy-seekproxy-pattern').value = p.pattern ?? 0;
    if (document.getElementById('dynamic-proxy-seekproxy-valid-code')) document.getElementById('dynamic-proxy-seekproxy-valid-code').value = p.valid_code ?? 0;
    if (document.getElementById('dynamic-proxy-seekproxy-state')) document.getElementById('dynamic-proxy-seekproxy-state').value = p.state || '';
    if (document.getElementById('dynamic-proxy-seekproxy-city')) document.getElementById('dynamic-proxy-seekproxy-city').value = p.city || '';
    if (document.getElementById('dynamic-proxy-seekproxy-break-type')) document.getElementById('dynamic-proxy-seekproxy-break-type').value = p.break_type || 1;
    if (document.getElementById('dynamic-proxy-seekproxy-time')) document.getElementById('dynamic-proxy-seekproxy-time').value = p.time || 5;
    if (document.getElementById('dynamic-proxy-seekproxy-country')) document.getElementById('dynamic-proxy-seekproxy-country').value = p.country || 'US';
    if (document.getElementById('dynamic-proxy-seekproxy-country-search')) document.getElementById('dynamic-proxy-seekproxy-country-search').value = p.country || '';
    if (document.getElementById('dynamic-proxy-seekproxy-state-search')) document.getElementById('dynamic-proxy-seekproxy-state-search').value = p.state || '';
    if (document.getElementById('dynamic-proxy-seekproxy-city-search')) document.getElementById('dynamic-proxy-seekproxy-city-search').value = p.city || '';
    if (document.getElementById('dynamic-proxy-scheme')) document.getElementById('dynamic-proxy-scheme').value = p.scheme || 'http';
    if (document.getElementById('dynamic-proxy-host')) document.getElementById('dynamic-proxy-host').value = p.host || 'proxy.haiwai-ip.com';
    if (document.getElementById('dynamic-proxy-port')) document.getElementById('dynamic-proxy-port').value = p.port || 1456;
    if (document.getElementById('dynamic-proxy-username')) document.getElementById('dynamic-proxy-username').value = p.username || '';
    if (document.getElementById('dynamic-proxy-country')) document.getElementById('dynamic-proxy-country').value = p.country || 'us';
    const stateOptions = document.getElementById('dynamic-proxy-seekproxy-state-options');
    const cityOptions = document.getElementById('dynamic-proxy-seekproxy-city-options');
    if (stateOptions && p.state) stateOptions.value = p.state;
    if (cityOptions && p.city) cityOptions.value = p.city;
}

// ============== Team Manager 服务管理 ==============

async function loadTmServices() {
    if (!elements.tmServicesTable) return;
    try {
        const services = await api.get('/tm-services');
        renderTmServicesTable(services);
    } catch (e) {
        elements.tmServicesTable.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--danger-color);">${e.message}</td></tr>`;
    }
}

function renderTmServicesTable(services) {
    if (!services || services.length === 0) {
        elements.tmServicesTable.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">暂无 Team Manager 服务，点击「添加服务」新增</td></tr>';
        return;
    }
    elements.tmServicesTable.innerHTML = services.map(s => `
        <tr>
            <td>${escapeHtml(s.name)}</td>
            <td style="font-size:0.85rem;color:var(--text-muted);">${escapeHtml(s.api_url)}</td>
            <td style="text-align:center;" title="${s.enabled ? '已启用' : '已禁用'}">${s.enabled ? '✅' : '⭕'}</td>
            <td style="text-align:center;">${s.priority}</td>
            <td style="white-space:nowrap;">
                <button class="btn btn-secondary btn-sm" onclick="editTmService(${s.id})">编辑</button>
                <button class="btn btn-secondary btn-sm" onclick="testTmServiceById(${s.id})">测试</button>
                <button class="btn btn-danger btn-sm" onclick="deleteTmService(${s.id}, '${escapeHtml(s.name)}')">删除</button>
            </td>
        </tr>
    `).join('');
}

function openTmServiceModal(service = null) {
    document.getElementById('tm-service-id').value = service ? service.id : '';
    document.getElementById('tm-service-name').value = service ? service.name : '';
    document.getElementById('tm-service-url').value = service ? service.api_url : '';
    document.getElementById('tm-service-key').value = '';
    document.getElementById('tm-service-priority').value = service ? service.priority : 0;
    document.getElementById('tm-service-enabled').checked = service ? service.enabled : true;
    if (service) {
        document.getElementById('tm-service-key').placeholder = service.has_key ? '已配置，留空保持不变' : '请输入 API Key';
    } else {
        document.getElementById('tm-service-key').placeholder = '请输入 API Key';
    }
    elements.tmServiceModalTitle.textContent = service ? '编辑 Team Manager 服务' : '添加 Team Manager 服务';
    elements.tmServiceEditModal.classList.add('active');
}

function closeTmServiceModal() {
    elements.tmServiceEditModal.classList.remove('active');
}

async function editTmService(id) {
    try {
        const service = await api.get(`/tm-services/${id}`);
        openTmServiceModal(service);
    } catch (e) {
        toast.error('获取服务信息失败: ' + e.message);
    }
}

async function handleSaveTmService(e) {
    e.preventDefault();
    const id = document.getElementById('tm-service-id').value;
    const name = document.getElementById('tm-service-name').value.trim();
    const apiUrl = document.getElementById('tm-service-url').value.trim();
    const apiKey = document.getElementById('tm-service-key').value.trim();
    const priority = parseInt(document.getElementById('tm-service-priority').value) || 0;
    const enabled = document.getElementById('tm-service-enabled').checked;

    if (!name || !apiUrl) {
        toast.error('名称和 API URL 不能为空');
        return;
    }
    if (!id && !apiKey) {
        toast.error('新增服务时 API Key 不能为空');
        return;
    }

    try {
        const payload = { name, api_url: apiUrl, priority, enabled };
        if (apiKey) payload.api_key = apiKey;

        if (id) {
            await api.patch(`/tm-services/${id}`, payload);
            toast.success('服务已更新');
        } else {
            payload.api_key = apiKey;
            await api.post('/tm-services', payload);
            toast.success('服务已添加');
        }
        closeTmServiceModal();
        loadTmServices();
    } catch (e) {
        toast.error('保存失败: ' + e.message);
    }
}

async function deleteTmService(id, name) {
    const confirmed = await confirm(`确定要删除 Team Manager 服务「${name}」吗？`);
    if (!confirmed) return;
    try {
        await api.delete(`/tm-services/${id}`);
        toast.success('已删除');
        loadTmServices();
    } catch (e) {
        toast.error('删除失败: ' + e.message);
    }
}

async function testTmServiceById(id) {
    try {
        const result = await api.post(`/tm-services/${id}/test`);
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (e) {
        toast.error('测试失败: ' + e.message);
    }
}

async function handleTestTmService() {
    const apiUrl = document.getElementById('tm-service-url').value.trim();
    const apiKey = document.getElementById('tm-service-key').value.trim();
    const id = document.getElementById('tm-service-id').value;

    if (!apiUrl) {
        toast.error('请先填写 API URL');
        return;
    }
    if (!id && !apiKey) {
        toast.error('请先填写 API Key');
        return;
    }

    elements.testTmServiceBtn.disabled = true;
    elements.testTmServiceBtn.textContent = '测试中...';

    try {
        let result;
        if (id && !apiKey) {
            result = await api.post(`/tm-services/${id}/test`);
        } else {
            result = await api.post('/tm-services/test-connection', { api_url: apiUrl, api_key: apiKey });
        }
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (e) {
        toast.error('测试失败: ' + e.message);
    } finally {
        elements.testTmServiceBtn.disabled = false;
        elements.testTmServiceBtn.textContent = '🔌 测试连接';
    }
}


// ============== CPA 服务管理 ==============

async function loadCpaServices() {
    if (!elements.cpaServicesTable) return;
    try {
        const services = await api.get('/cpa-services');
        renderCpaServicesTable(services);
    } catch (e) {
        elements.cpaServicesTable.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--danger-color);">${e.message}</td></tr>`;
    }
}

function renderCpaServicesTable(services) {
    if (!services || services.length === 0) {
        elements.cpaServicesTable.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:20px;">暂无 CPA 服务，点击「添加服务」新增</td></tr>';
        return;
    }
    elements.cpaServicesTable.innerHTML = services.map(s => `
        <tr>
            <td>${escapeHtml(s.name)}</td>
            <td style="font-size:0.85rem;color:var(--text-muted);">${escapeHtml(s.api_url)}</td>
            <td style="text-align:center;">${s.include_proxy_url ? '🟢' : '⚪'}</td>
            <td style="text-align:center;" title="${s.enabled ? '已启用' : '已禁用'}">${s.enabled ? '✅' : '⭕'}</td>
            <td style="text-align:center;">${s.priority}</td>
            <td style="white-space:nowrap;">
                <button class="btn btn-secondary btn-sm" onclick="editCpaService(${s.id})">编辑</button>
                <button class="btn btn-secondary btn-sm" onclick="testCpaServiceById(${s.id})">测试</button>
                <button class="btn btn-danger btn-sm" onclick="deleteCpaService(${s.id}, '${escapeHtml(s.name)}')">删除</button>
            </td>
        </tr>
    `).join('');
}

function openCpaServiceModal(service = null) {
    document.getElementById('cpa-service-id').value = service ? service.id : '';
    document.getElementById('cpa-service-name').value = service ? service.name : '';
    document.getElementById('cpa-service-url').value = service ? service.api_url : '';
    document.getElementById('cpa-service-token').value = '';
    document.getElementById('cpa-service-priority').value = service ? service.priority : 0;
    document.getElementById('cpa-service-enabled').checked = service ? service.enabled : true;
    document.getElementById('cpa-service-include-proxy-url').checked = service ? !!service.include_proxy_url : false;
    elements.cpaServiceModalTitle.textContent = service ? '编辑 CPA 服务' : '添加 CPA 服务';
    elements.cpaServiceEditModal.classList.add('active');
}

function closeCpaServiceModal() {
    elements.cpaServiceEditModal.classList.remove('active');
}

async function editCpaService(id) {
    try {
        const service = await api.get(`/cpa-services/${id}`);
        openCpaServiceModal(service);
    } catch (e) {
        toast.error('获取服务信息失败: ' + e.message);
    }
}

async function handleSaveCpaService(e) {
    e.preventDefault();
    const id = document.getElementById('cpa-service-id').value;
    const name = document.getElementById('cpa-service-name').value.trim();
    const apiUrl = document.getElementById('cpa-service-url').value.trim();
    const apiToken = document.getElementById('cpa-service-token').value.trim();
    const priority = parseInt(document.getElementById('cpa-service-priority').value) || 0;
    const enabled = document.getElementById('cpa-service-enabled').checked;
    const includeProxyUrl = document.getElementById('cpa-service-include-proxy-url').checked;

    if (!name || !apiUrl) {
        toast.error('名称和 API URL 不能为空');
        return;
    }
    if (!id && !apiToken) {
        toast.error('新增服务时 API Token 不能为空');
        return;
    }

    try {
        const payload = { name, api_url: apiUrl, priority, enabled, include_proxy_url: includeProxyUrl };
        if (apiToken) payload.api_token = apiToken;

        if (id) {
            await api.patch(`/cpa-services/${id}`, payload);
            toast.success('服务已更新');
        } else {
            payload.api_token = apiToken;
            await api.post('/cpa-services', payload);
            toast.success('服务已添加');
        }
        closeCpaServiceModal();
        loadCpaServices();
    } catch (e) {
        toast.error('保存失败: ' + e.message);
    }
}

async function deleteCpaService(id, name) {
    const confirmed = await confirm(`确定要删除 CPA 服务「${name}」吗？`);
    if (!confirmed) return;
    try {
        await api.delete(`/cpa-services/${id}`);
        toast.success('已删除');
        loadCpaServices();
    } catch (e) {
        toast.error('删除失败: ' + e.message);
    }
}

async function testCpaServiceById(id) {
    try {
        const result = await api.post(`/cpa-services/${id}/test`);
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (e) {
        toast.error('测试失败: ' + e.message);
    }
}

async function handleTestCpaService() {
    const apiUrl = document.getElementById('cpa-service-url').value.trim();
    const apiToken = document.getElementById('cpa-service-token').value.trim();
    const id = document.getElementById('cpa-service-id').value;

    if (!apiUrl) {
        toast.error('请先填写 API URL');
        return;
    }
    // 新增时必须有 token，编辑时 token 可为空（用已保存的）
    if (!id && !apiToken) {
        toast.error('请先填写 API Token');
        return;
    }

    elements.testCpaServiceBtn.disabled = true;
    elements.testCpaServiceBtn.textContent = '测试中...';

    try {
        let result;
        if (id && !apiToken) {
            // 编辑时未填 token，直接测试已保存的服务
            result = await api.post(`/cpa-services/${id}/test`);
        } else {
            result = await api.post('/cpa-services/test-connection', { api_url: apiUrl, api_token: apiToken });
        }
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (e) {
        toast.error('测试失败: ' + e.message);
    } finally {
        elements.testCpaServiceBtn.disabled = false;
        elements.testCpaServiceBtn.textContent = '🔌 测试连接';
    }
}

// ============================================================================
// Sub2API 服务管理
// ============================================================================

let _sub2apiEditingId = null;

async function loadSub2ApiServices() {
    try {
        const services = await api.get('/sub2api-services');
        renderSub2ApiServices(services);
    } catch (e) {
        if (elements.sub2ApiServicesTable) {
            elements.sub2ApiServicesTable.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">加载失败</td></tr>';
        }
    }
}

function renderSub2ApiServices(services) {
    if (!elements.sub2ApiServicesTable) return;
    if (!services || services.length === 0) {
        elements.sub2ApiServicesTable.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">暂无 Sub2API 服务，点击「添加服务」新增</td></tr>';
        return;
    }
    elements.sub2ApiServicesTable.innerHTML = services.map(s => `
        <tr>
            <td>${escapeHtml(s.name)}</td>
            <td style="font-size:0.85rem;color:var(--text-muted);">${escapeHtml(s.api_url)}</td>
            <td style="text-align:center;" title="${s.enabled ? '已启用' : '已禁用'}">${s.enabled ? '✅' : '⭕'}</td>
            <td style="text-align:center;">${s.priority}</td>
            <td style="white-space:nowrap;">
                <button class="btn btn-secondary btn-sm" onclick="editSub2ApiService(${s.id})">编辑</button>
                <button class="btn btn-secondary btn-sm" onclick="testSub2ApiServiceById(${s.id})">测试</button>
                <button class="btn btn-danger btn-sm" onclick="deleteSub2ApiService(${s.id}, '${escapeHtml(s.name)}')">删除</button>
            </td>
        </tr>
    `).join('');
}

function openSub2ApiServiceModal(svc = null) {
    _sub2apiEditingId = svc ? svc.id : null;
    elements.sub2ApiServiceModalTitle.textContent = svc ? '编辑 Sub2API 服务' : '添加 Sub2API 服务';
    elements.sub2ApiServiceForm.reset();
    document.getElementById('sub2api-service-id').value = svc ? svc.id : '';
    if (svc) {
        document.getElementById('sub2api-service-name').value = svc.name || '';
        document.getElementById('sub2api-service-url').value = svc.api_url || '';
        document.getElementById('sub2api-service-priority').value = svc.priority ?? 0;
        document.getElementById('sub2api-service-enabled').checked = svc.enabled !== false;
        document.getElementById('sub2api-service-key').placeholder = svc.has_key ? '已配置，留空保持不变' : '请输入 API Key';
    }
    elements.sub2ApiServiceEditModal.classList.add('active');
}

function closeSub2ApiServiceModal() {
    elements.sub2ApiServiceEditModal.classList.remove('active');
    elements.sub2ApiServiceForm.reset();
    _sub2apiEditingId = null;
}

async function editSub2ApiService(id) {
    try {
        const svc = await api.get(`/sub2api-services/${id}`);
        openSub2ApiServiceModal(svc);
    } catch (e) {
        toast.error('加载失败: ' + e.message);
    }
}

async function deleteSub2ApiService(id, name) {
    if (!confirm(`确认删除 Sub2API 服务「${name}」？`)) return;
    try {
        await api.delete(`/sub2api-services/${id}`);
        toast.success('服务已删除');
        loadSub2ApiServices();
    } catch (e) {
        toast.error('删除失败: ' + e.message);
    }
}

async function handleSaveSub2ApiService(e) {
    e.preventDefault();
    const id = document.getElementById('sub2api-service-id').value;
    const data = {
        name: document.getElementById('sub2api-service-name').value,
        api_url: document.getElementById('sub2api-service-url').value,
        api_key: document.getElementById('sub2api-service-key').value || undefined,
        priority: parseInt(document.getElementById('sub2api-service-priority').value) || 0,
        enabled: document.getElementById('sub2api-service-enabled').checked,
    };
    if (!id && !data.api_key) {
        toast.error('请填写 API Key');
        return;
    }
    if (!data.api_key) delete data.api_key;

    try {
        if (id) {
            await api.patch(`/sub2api-services/${id}`, data);
            toast.success('服务已更新');
        } else {
            await api.post('/sub2api-services', data);
            toast.success('服务已添加');
        }
        closeSub2ApiServiceModal();
        loadSub2ApiServices();
    } catch (e) {
        toast.error('保存失败: ' + e.message);
    }
}

async function testSub2ApiServiceById(id) {
    try {
        const result = await api.post(`/sub2api-services/${id}/test`);
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (e) {
        toast.error('测试失败: ' + e.message);
    }
}

async function handleTestSub2ApiService() {
    const apiUrl = document.getElementById('sub2api-service-url').value.trim();
    const apiKey = document.getElementById('sub2api-service-key').value.trim();
    const id = document.getElementById('sub2api-service-id').value;

    if (!apiUrl) {
        toast.error('请先填写 API URL');
        return;
    }
    if (!id && !apiKey) {
        toast.error('请先填写 API Key');
        return;
    }

    elements.testSub2ApiServiceBtn.disabled = true;
    elements.testSub2ApiServiceBtn.textContent = '测试中...';

    try {
        let result;
        if (id && !apiKey) {
            result = await api.post(`/sub2api-services/${id}/test`);
        } else {
            result = await api.post('/sub2api-services/test-connection', { api_url: apiUrl, api_key: apiKey });
        }
        if (result.success) {
            const extra = result.https_openai_message
                ? `
OpenAI HTTPS: ${result.https_openai_ok ? '可用' : '不可用'} - ${result.https_openai_message}`
                : '';
            toast.success(`${result.message}${extra}`);
        } else {
            toast.error(result.message);
        }
    } catch (e) {
        toast.error('测试失败: ' + e.message);
    } finally {
        elements.testSub2ApiServiceBtn.disabled = false;
        elements.testSub2ApiServiceBtn.textContent = '🔌 测试连接';
    }
}

function escapeHtml(text) {
    if (!text) return '';
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}
