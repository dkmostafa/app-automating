"""The Android emulator service: the product's operations, in the product's words.

Every method here is one thing a user of this module wants to do, named the way
they would say it, and its arguments are the ones they actually have -- an AVD
name, a package id. The domain's ``*Request`` dataclasses are built *inside* the
service, which is the point: "get the available devices" and "get all devices"
are the same infrastructure call with one flag flipped, and hiding that flag is
what makes them two honest operations instead of one leaky one.

Dependency injection, per Rule 0 §4: the three collaborators arrive through the
constructor, typed as the domain's ports. This class never names an adapter, an
Android SDK tool, or a subprocess. Swap the ports for a remote device farm and
the service is unchanged -- and because the ports are segregated (Rule 0 §3),
what it holds is exactly the authority it needs and no more.
"""

from __future__ import annotations

from ...domain.models import (
    CreateEmulatorRequest,
    CreateEmulatorResult,
    DeleteEmulatorRequest,
    DeleteEmulatorResult,
    InstallSystemImageRequest,
    InstallSystemImageResult,
    ListDevicesRequest,
    ListDevicesResult,
    ListEmulatorsRequest,
    ListEmulatorsResult,
    RenameEmulatorRequest,
    RenameEmulatorResult,
    StartEmulatorRequest,
    StartEmulatorResult,
    StopEmulatorRequest,
    StopEmulatorResult,
)
from ...domain.ports import DeviceCatalog, EmulatorLifecycle, SystemImageInstaller

__all__ = ["AndroidEmulatorService"]


class AndroidEmulatorService:
    """Android emulators, as the rest of the product sees them."""

    def __init__(
        self,
        devices: DeviceCatalog,
        emulators: EmulatorLifecycle,
        images: SystemImageInstaller,
    ) -> None:
        self._devices = devices
        self._emulators = emulators
        self._images = images

    # -- reading -----------------------------------------------------------

    async def get_available_devices(self, *, resolve_avd_names: bool = True) -> ListDevicesResult:
        """Devices that will actually accept commands right now.

        Excludes anything offline or unauthorised. ``resolve_avd_names`` costs
        one extra call per emulator and is the only way to map a serial back to
        the AVD running on it; turn it off when the names are not needed.
        """
        return await self._devices.list_devices(
            ListDevicesRequest(
                resolve_avd_names=resolve_avd_names,
                include_unavailable=False,
            )
        )

    async def get_all_devices(self, *, resolve_avd_names: bool = True) -> ListDevicesResult:
        """Every device the host knows about, offline and unauthorised included.

        The superset of :meth:`get_available_devices` -- use this one when the
        question is "what is attached", not "what can I drive".
        """
        return await self._devices.list_devices(
            ListDevicesRequest(
                resolve_avd_names=resolve_avd_names,
                include_unavailable=True,
            )
        )

    async def get_installed_emulators(
        self, *, include_unloadable: bool = True
    ) -> ListEmulatorsResult:
        """Every AVD defined on this host, whether or not it is running.

        The catalogue behind :meth:`run_emulator`: an AVD name only shows up in
        :meth:`get_available_devices` once something has booted it, so this is
        the only way to answer "what could I start".

        ``include_unloadable`` keeps the AVDs the backend could not read -- a
        missing system image, a retired device profile. They exist on disk and
        cannot be booted, and dropping them would turn a broken AVD into a
        missing one, which is a different problem with a different fix.
        """
        return await self._devices.list_emulators(
            ListEmulatorsRequest(include_unloadable=include_unloadable)
        )

    # -- running -----------------------------------------------------------

    async def run_emulator(
        self,
        name: str,
        *,
        wait_for_boot: bool = True,
        cold_boot: bool = False,
        wipe_data: bool = False,
        boot_timeout_seconds: float | None = None,
    ) -> StartEmulatorResult:
        """Boot the named emulator with a visible window.

        Waits for the device to finish booting by default; without that the call
        returns as soon as the serial attaches and ``booted`` comes back False.
        """
        return await self._emulators.start_emulator(
            StartEmulatorRequest(
                name=name,
                headless=False,
                wait_for_boot=wait_for_boot,
                cold_boot=cold_boot,
                wipe_data=wipe_data,
                boot_timeout_seconds=boot_timeout_seconds,
            )
        )

    async def run_emulator_without_window(
        self,
        name: str,
        *,
        wait_for_boot: bool = True,
        cold_boot: bool = False,
        wipe_data: bool = False,
        boot_timeout_seconds: float | None = None,
    ) -> StartEmulatorResult:
        """Boot the named emulator headless, for automation.

        The same operation as :meth:`run_emulator` with no window, no audio and
        no boot animation -- what a CI machine or a test run wants.
        """
        return await self._emulators.start_emulator_headless(
            StartEmulatorRequest(
                name=name,
                headless=True,
                wait_for_boot=wait_for_boot,
                cold_boot=cold_boot,
                wipe_data=wipe_data,
                boot_timeout_seconds=boot_timeout_seconds,
            )
        )

    async def stop_emulator(
        self,
        device_id: str,
        *,
        wait_for_exit: bool = True,
        timeout_seconds: float | None = None,
    ) -> StopEmulatorResult:
        """Shut down the emulator attached as ``device_id``.

        Addressed by serial rather than by AVD name because the serial is what
        the caller is holding after :meth:`run_emulator` returned, and because
        the serial is what every other device operation takes.

        Waits for the serial to leave the device list by default; without that
        the call returns while the device is still going down and ``stopped``
        comes back False.
        """
        return await self._emulators.stop_emulator(
            StopEmulatorRequest(
                device_id=device_id,
                wait_for_exit=wait_for_exit,
                timeout_seconds=timeout_seconds,
            )
        )

    # -- provisioning ------------------------------------------------------

    async def create_device(
        self,
        name: str,
        system_image: str,
        device: str,
        *,
        sdcard_size: str | None = None,
        abi: str | None = None,
        replace_existing: bool = False,
    ) -> CreateEmulatorResult:
        """Create a new virtual device.

        ``system_image`` is a package id such as
        ``system-images;android-34;google_apis;x86_64``, and it must already be
        installed -- :meth:`download_image` puts it there. ``device`` is a
        hardware profile like ``pixel_6``.

        ``replace_existing`` overwrites a device of the same name instead of
        raising; it is spelled out here rather than passed as ``force`` because
        a caller should have to say what it is forcing.
        """
        return await self._emulators.create_emulator(
            CreateEmulatorRequest(
                name=name,
                system_image=system_image,
                device=device,
                sdcard_size=sdcard_size,
                abi=abi,
                force=replace_existing,
            )
        )

    async def download_image(
        self,
        system_image: str,
        *,
        accept_licenses: bool = True,
        reinstall: bool = False,
    ) -> InstallSystemImageResult:
        """Download and unpack a system image.

        A no-op returning ``already_installed=True`` when the image is already
        on disk, unless ``reinstall`` forces the download. Licences are accepted
        by default because the installer blocks forever waiting on the prompt
        otherwise.
        """
        return await self._images.install_system_image(
            InstallSystemImageRequest(
                system_image=system_image,
                accept_licenses=accept_licenses,
                reinstall=reinstall,
            )
        )

    async def delete_emulator(
        self, name: str, *, stop_if_running: bool = False
    ) -> DeleteEmulatorResult:
        """Delete an AVD and everything it holds: snapshots, userdata, sdcard.

        Irreversible, and the inverse of :meth:`create_device` rather than of
        :meth:`run_emulator` -- stopping an emulator leaves the AVD on disk,
        this removes it.

        ``stop_if_running`` shuts the AVD down first. Without it a running AVD
        raises instead of being deleted out from under the process using it,
        which is the safer default: the caller has to say that it knows the
        device is in use.
        """
        return await self._emulators.delete_emulator(
            DeleteEmulatorRequest(name=name, stop_if_running=stop_if_running)
        )

    async def rename_emulator(
        self, name: str, new_name: str, *, stop_if_running: bool = False
    ) -> RenameEmulatorResult:
        """Give an existing AVD a different name, keeping everything on it.

        The opposite of :meth:`delete_emulator` in cost: nothing is recreated
        and nothing is lost -- snapshots, installed apps, userdata and sdcard
        all survive, because the AVD's payload directory is moved rather than
        rebuilt. Only the name changes.

        Renaming to a name that is already taken raises, and that includes the
        AVD's own current name: a rename that changes nothing is a mistake worth
        reporting rather than a call worth pretending succeeded.

        ``stop_if_running`` shuts the AVD down first. Without it a running AVD
        raises, which is the safer default here for a stronger reason than in
        :meth:`delete_emulator` -- the payload directory moves during a rename,
        and moving it out from under a live emulator corrupts the device rather
        than merely surprising its user.

        Note that a running emulator's device serial is unaffected either way: a
        serial is assigned from the console port at boot and has never been
        derived from the AVD's name.
        """
        return await self._emulators.rename_emulator(
            RenameEmulatorRequest(name=name, new_name=new_name, stop_if_running=stop_if_running)
        )
