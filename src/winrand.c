/* -*- C -*- */
/*
 * Uses Windows CryptoAPI CryptGenRandom to get random bytes
 *
 * Distribute and use freely; there are no restrictions on further 
 * dissemination and usage except those imposed by the laws of your 
 * country of residence.  This software is provided "as is" without
 * warranty of fitness for use or suitability for any purpose, express
 * or implied. Use at your own risk or not at all. 
 *
 */

/* Author: Mark Moraes */

#include "Python.h"

#ifdef MS_WIN32

#define _WIN32_WINNT 0x400
#define WINSOCK

#include <windows.h>
#include <wincrypt.h>

LPVOID PrintWindowsError(char* msg, DWORD error) 
{
  LPVOID lpMsgBuf;
  if (!FormatMessage(FORMAT_MESSAGE_ALLOCATE_BUFFER | 
		     FORMAT_MESSAGE_FROM_SYSTEM | 
		     FORMAT_MESSAGE_IGNORE_INSERTS,
		     NULL, error,
		     MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT), // Default language
		     (LPTSTR) &lpMsgBuf,
		     0,
		     NULL )) {
    fprintf(stderr, "FormatMessage had an error when processing this error (%d) and this message (%s)!", error, msg);
  }
  fprintf(stderr, "%s: %s\n", msg, lpMsgBuf); 
  LocalFree(lpMsgBuf);
}


static char winrandom__doc__[] =
"winrandom(nbytes): Returns nbytes of random data from Windows CryptGenRandom,"
"a cryptographically strong pseudo-random generator using system entropy";

static PyObject *
winrandom(PyObject *self, PyObject *args)
{
  HCRYPTPROV hcp = 0;
  int n, nbytes;
  PyObject *res;
  char *buf;

  if (!PyArg_ParseTuple(args, "i", &n)) {
    return NULL;
  }
  /* Just in case char != BYTE */
  nbytes = (n * sizeof(char)) / sizeof(BYTE);
  if (nbytes <= 0) {
    PyErr_SetString(PyExc_ValueError, "nbytes must be positive number");
    return NULL;
  }
  if ((buf = (char *) PyMem_Malloc(nbytes)) == NULL)
    return PyErr_NoMemory();

  if (! CryptAcquireContext(&hcp, NULL, NULL, PROV_RSA_FULL, 0)) {
    // If the last error was a bad keyset, then it might be
    // because we need to generate a keyset, so we call
    // CryptAcquireContext again in order to try to create
    // a key set this time.
    DWORD lastError = GetLastError();
    if (lastError == NTE_BAD_KEYSET) {
      if (!CryptAcquireContext(&hcp, NULL, NULL, PROV_RSA_FULL,
                               CRYPT_NEWKEYSET)) {
        lastError = GetLastError();
      } else {
        lastError = 0;
      }
    }
    if (lastError != 0) {
      PyErr_Format(PyExc_SystemError,
                   "CryptAcquireContext failed, error %i",
                   lastError);
      PrintWindowsError("CryptAcquireContext failed", lastError);
      PyMem_Free(buf);
      return NULL;
    }
  } 
  if (! CryptGenRandom(hcp, (DWORD) nbytes, (BYTE *) buf)) {
    PyErr_Format(PyExc_SystemError,
                 "CryptGenRandom failed, error %i",
                 GetLastError());
    PyMem_Free(buf);
    (void) CryptReleaseContext(hcp, 0);
    return NULL;
  }
  if (! CryptReleaseContext(hcp, 0)) {
    PyErr_Format(PyExc_SystemError,
                 "CryptReleaseContext failed, error %i",
                 GetLastError());
    return NULL;
  }
  res = PyString_FromStringAndSize(buf, n);
  PyMem_Free(buf);
  return res;
}

static PyMethodDef WRMethods[] = {
  {"winrandom", (PyCFunction) winrandom, METH_VARARGS, winrandom__doc__},
  {NULL,	    NULL}	 /* Sentinel */
};


void
initwinrandom()
{
  (void) Py_InitModule("winrandom", WRMethods);
}

#endif /* MS_WIN32 */
