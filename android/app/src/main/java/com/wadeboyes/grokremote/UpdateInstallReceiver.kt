package com.wadeboyes.grokremote

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller

/** PackageInstaller reports session status here. The activity starts any confirm screen. */
class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(
            PackageInstaller.EXTRA_STATUS,
            PackageInstaller.STATUS_FAILURE,
        )
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty()
        val confirm = if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
        } else {
            null
        }
        AppUpdater.dispatchInstallStatus(status, message, confirm)
    }

    companion object {
        const val ACTION = "com.wadeboyes.grokremote.UPDATE_INSTALL_STATUS"
    }
}
