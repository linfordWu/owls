<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { setApiKey, hasApiKey } from "@/api/client";
import { fetchAuthStatus, loginWithPassword, setupPassword } from "@/api/auth";

const { t } = useI18n();
const router = useRouter();

// Read token saved by main.ts (before router strips URL params)
const urlToken = (window as any).__LOGIN_TOKEN__ || "";

const token = ref(urlToken);
const username = ref("");
const password = ref("");
const confirmPassword = ref("");
const loading = ref(false);
const errorMsg = ref("");
const setupTokenVerified = ref(false);

type LoginMode = "checking" | "token" | "password" | "setup";
const loginMode = ref<LoginMode>("checking");
const hasPasswordLogin = ref(false);

onMounted(async () => {
  try {
    const status = await fetchAuthStatus();
    hasPasswordLogin.value = status.hasPasswordLogin;
    if (status.hasPasswordLogin) {
      if (hasApiKey()) {
        router.replace("/owls/chat");
        return;
      }
      loginMode.value = "password";
    } else if (hasApiKey()) {
      setupTokenVerified.value = true;
      loginMode.value = "setup";
    } else {
      loginMode.value = "token";
      if (urlToken) {
        await handleTokenLogin();
      }
    }
  } catch {
    loginMode.value = "token";
  }
});

async function handleLogin() {
  if (loginMode.value === "token") {
    await handleTokenLogin();
  } else if (loginMode.value === "password") {
    await handlePasswordLogin();
  } else if (loginMode.value === "setup") {
    await handleSetupPassword();
  }
}

async function handleTokenLogin() {
  const key = token.value.trim();
  if (!key) {
    errorMsg.value = t("login.tokenRequired");
    return;
  }

  loading.value = true;
  errorMsg.value = "";

  try {
    const res = await fetch("/api/sessions", {
      headers: { Authorization: `Bearer ${key}` },
    });

    if (res.status === 401) {
      errorMsg.value = t("login.invalidToken");
      loading.value = false;
      return;
    }

    setApiKey(key);
    if (!hasPasswordLogin.value) {
      setupTokenVerified.value = true;
      loginMode.value = "setup";
      password.value = "";
      confirmPassword.value = "";
      return;
    }
    router.replace("/owls/chat");
  } catch {
    errorMsg.value = t("login.connectionFailed");
  } finally {
    loading.value = false;
  }
}

async function handlePasswordLogin() {
  if (!username.value.trim() || !password.value) {
    errorMsg.value = t("login.credentialsRequired");
    return;
  }

  loading.value = true;
  errorMsg.value = "";

  try {
    const session = await loginWithPassword(username.value.trim(), password.value);
    setApiKey(session.token);
    router.replace("/owls/chat");
  } catch (err: any) {
    errorMsg.value = err.message || t("login.invalidCredentials");
  } finally {
    loading.value = false;
  }
}

async function handleSetupPassword() {
  if (!setupTokenVerified.value) {
    errorMsg.value = t("login.tokenRequired");
    loginMode.value = "token";
    return;
  }
  if (!username.value.trim() || !password.value || !confirmPassword.value) {
    errorMsg.value = t("login.credentialsRequired");
    return;
  }
  if (username.value.trim().length < 2) {
    errorMsg.value = t("login.usernameTooShort");
    return;
  }
  if (password.value.length < 6) {
    errorMsg.value = t("login.passwordTooShort");
    return;
  }
  if (password.value !== confirmPassword.value) {
    errorMsg.value = t("login.passwordMismatch");
    return;
  }

  loading.value = true;
  errorMsg.value = "";
  try {
    const session = await setupPassword(username.value.trim(), password.value);
    if (session?.token) setApiKey(session.token);
    router.replace("/owls/chat");
  } catch (err: any) {
    errorMsg.value = err.message || t("common.saveFailed");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-view">
    <div class="login-card">
      <div class="login-logo">
        <img src="/owls-log.png" alt="OWLS" width="92" height="92" />
      </div>
      <h1 class="login-title">{{ t("login.title") }}</h1>
      <p class="login-desc">
        {{ loginMode === "setup" ? t("login.forceSetupDescription") : t("login.description") }}
      </p>

      <div v-if="loginMode !== 'checking'" class="login-badge">
        {{ loginMode === "password" ? t("login.passwordLogin") : loginMode === "setup" ? t("login.setupPassword") : t("login.tokenLogin") }}
      </div>

      <form v-if="loginMode !== 'checking'" class="login-form" @submit.prevent="handleLogin">
        <template v-if="loginMode === 'token'">
          <input
            v-model="token"
            type="password"
            class="login-input"
            :placeholder="t('login.placeholder')"
            autofocus
          />
        </template>

        <template v-if="loginMode === 'password'">
          <input
            v-model="username"
            type="text"
            class="login-input"
            :placeholder="t('login.usernamePlaceholder')"
            autofocus
          />
          <input
            v-model="password"
            type="password"
            class="login-input"
            :placeholder="t('login.passwordPlaceholder')"
            @keyup.enter="handleLogin"
          />
        </template>

        <template v-if="loginMode === 'setup'">
          <input
            v-model="username"
            type="text"
            class="login-input"
            :placeholder="t('login.usernamePlaceholder')"
            autofocus
          />
          <input
            v-model="password"
            type="password"
            class="login-input"
            :placeholder="t('login.newPassword')"
          />
          <input
            v-model="confirmPassword"
            type="password"
            class="login-input"
            :placeholder="t('login.confirmPassword')"
            @keyup.enter="handleLogin"
          />
        </template>

        <div v-if="errorMsg" class="login-error">{{ errorMsg }}</div>
        <button type="submit" class="login-btn" :disabled="loading">
          {{ loading ? "..." : loginMode === "setup" ? t("login.setupPassword") : t("login.submit") }}
        </button>
      </form>
      <div v-else class="login-loading">{{ t("common.loading") }}</div>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use "@/styles/variables" as *;

.login-view {
  height: calc(100 * var(--vh));
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(circle at 12% 16%, rgba(255, 122, 26, 0.13), transparent 28%),
    radial-gradient(circle at 84% 18%, rgba(216, 230, 90, 0.20), transparent 24%),
    linear-gradient(135deg, $bg-primary, $bg-secondary);
}

.login-card {
  width: 480px;
  max-width: calc(100vw - 32px);
  padding: 56px;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  background: color-mix(in srgb, var(--bg-card) 92%, transparent);
  box-shadow: 0 28px 70px rgba(var(--accent-primary-rgb), 0.16);
  backdrop-filter: blur(18px);
  text-align: center;

  @media (max-width: $breakpoint-mobile) {
    padding: 32px 24px;
  }
}

.login-logo {
  margin-bottom: 24px;

  img {
    filter: drop-shadow(0 18px 28px rgba(95, 183, 100, 0.28));
  }
}

.login-title {
  font-size: 26px;
  font-weight: 600;
  color: $text-primary;
  margin: 0 0 10px;
}

.login-desc {
  font-size: 14px;
  color: $text-muted;
  margin: 0 0 32px;
  line-height: 1.6;
}

.login-badge {
  display: inline-flex;
  margin-bottom: 22px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid rgba(var(--accent-primary-rgb), 0.20);
  background: rgba(var(--accent-primary-rgb), 0.08);
  color: $accent-primary;
  font-size: 12px;
  font-weight: 700;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login-input {
  width: 100%;
  padding: 14px 16px;
  border: 1px solid $border-color;
  border-radius: $radius-sm;
  font-size: 15px;
  color: $text-primary;
  background: $bg-input;
  outline: none;
  transition: border-color $transition-fast;
  box-sizing: border-box;
  font-family: $font-code;

  &::placeholder {
    color: $text-muted;
  }

  &:focus {
    border-color: $accent-primary;
  }
}

.login-loading {
  color: $text-muted;
  font-size: 13px;
}

.login-error {
  font-size: 13px;
  color: $error;
  text-align: left;
}

.login-btn {
  width: 100%;
  padding: 14px;
  border: none;
  border-radius: $radius-sm;
  background: linear-gradient(135deg, $accent-primary, $accent-hover);
  color: var(--text-on-accent);
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity $transition-fast;

  &:hover {
    opacity: 0.85;
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}
</style>
