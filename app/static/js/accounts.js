
const ACCOUNT_STATUS_LABELS = {
  pending_qr: 'بانتظار رمز QR',
  waiting_scan: 'بانتظار المسح',
  waiting_2fa: 'بانتظار التحقق الثنائي',
  active: 'نشط',
  qr_expired: 'انتهت صلاحية QR',
  unauthorized: 'غير مصرح',
  restricted: 'مقيد',
  disconnected: 'غير متصل',
  removed: 'محذوف'
};

const addBtn = document.getElementById('addAccount');

if (addBtn) {
  addBtn.addEventListener('click', async () => {
    addBtn.disabled = true;
    try {
      const response = await fetch('/accounts/add', { method: 'POST' });
      const payload = await response.json();
      if (!response.ok) {
        alert(payload.error || 'تعذر إضافة الحساب');
        return;
      }

      const box = document.getElementById('qrBox');
      const status = document.getElementById('qrStatus');
      const image = document.getElementById('qrImage');
      box.classList.remove('d-none');
      status.textContent = 'جاري إنشاء رمز QR...';
      image.removeAttribute('src');
      image.classList.add('d-none');

      const timer = setInterval(async () => {
        try {
          const qrResponse = await fetch(`/api/qr/${payload.token}`, { cache: 'no-store' });
          const qr = await qrResponse.json();
          const labels = {
            preparing: 'جاري إنشاء رمز QR...',
            waiting_scan: 'امسح الرمز من تيليجرام: الإعدادات ← الأجهزة ← ربط جهاز',
            authorized: 'تم ربط الحساب بنجاح',
            expired: 'انتهت صلاحية الرمز، أعد المحاولة',
            waiting_2fa: 'الحساب يتطلب التحقق بخطوتين',
            error: `تعذر الربط: ${qr.error || 'خطأ غير معروف'}`
          };
          status.textContent = labels[qr.status] || qr.status || 'preparing';

          if (qr.qr_image) {
            image.src = qr.qr_image;
            image.classList.remove('d-none');
          }

          if (['authorized', 'expired', 'error', 'waiting_2fa'].includes(qr.status)) {
            clearInterval(timer);
            addBtn.disabled = false;
            if (qr.status === 'authorized') window.location.reload();
          }
        } catch (error) {
          console.error(error);
        }
      }, 1000);
    } finally {
      setTimeout(() => { addBtn.disabled = false; }, 2000);
    }
  });
}

setInterval(async () => {
  const response = await fetch('/api/accounts/status', { cache: 'no-store' });
  if (!response.ok) return;
  const payload = await response.json();
  for (const account of payload.accounts) {
    const element = document.querySelector(`[data-account-status="${account.id}"]`);
    if (!element) continue;
    element.textContent = ACCOUNT_STATUS_LABELS[account.status] || account.status;
    element.className = `status-pill status-${account.status}`;
  }
}, 10000);
